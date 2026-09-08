from pycomm3 import LogixDriver
from pycomm3.exceptions import CommError
import time
import threading
import psycopg2
import logging
from dotenv import load_dotenv
import os
import datetime
from pathlib import Path
import pandas as pd

class SharedVelocidad:
    def __init__(self):
        self.velocidad = 0
        self.lock = threading.Lock()

    def set(self, value):
        with self.lock:
            self.velocidad = value

    def get(self):
        with self.lock:
            return self.velocidad

def insertCounter(conn, schema: str,ip: str, tag: str, oldValue: int, newValue: int, diff:int, vel: float):
    """Funcion para insertar en la db"""
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"INSERT INTO {schema}.paradas (ip, tag, old_value, new_value, dif, status_noria, vel) VALUES (%s, %s, %s, %s, %s, %s, %s)", (ip, tag, oldValue, newValue, diff, status_noria.is_set(), vel))
            conn.commit()
    except psycopg2.Error as ex:
        logger.error(f'{ip} en query: {ex}')
        return

def PLCHandler(conn_data : dict[str], ip: str, contadores: dict[str, dict[str, int]]):
    """Funcion para conectarse al PLC y leer los contadores"""
    global velocidad
    keys = list(contadores.keys())
    while not stop_flag.is_set(): # ciclo para reconectarse al PLC o a la db
        try:
            with LogixDriver(ip) as plc, psycopg2.connect(host=conn_data["host"], dbname=conn_data["dbname"], user=conn_data["user"], password=conn_data["password"]) as conn:
                logger.info(f'Conectado a {ip} para leer los contadores de paradas')
                while not stop_flag.is_set(): # ciclo para leer los contadores
                    values_counters_list = plc.read(*keys) # leer los contadores
                    if values_counters_list:
                        values_counters_list = [int(x[1]) for x in values_counters_list]
                    else:
                        continue
                    values_counters_dict = dict(zip(keys, values_counters_list)) # armar un diccionario con los valores leidos
                    for key, value in contadores.items():
                        # para inicializar el contador
                        if value['value'] == 0:
                            value['value'] = values_counters_dict[key]
                            continue

                        # si el contador cambio, insertar en db
                        if values_counters_dict[key] != value['value']:
                            vel = velocidad.get()
                            threading.Thread(
                                target=insertCounter, 
                                args=(conn, conn_data["schema"] ,ip, key, value['value'], values_counters_dict[key], values_counters_dict[key] - value['value'], vel)
                                ).start()
                            logger.info(f'{ip} : {key} : {values_counters_dict[key] - value["value"]}')
                            value['value'] = values_counters_dict[key]
                    time.sleep(0.5)
        except CommError as ex:
            logger.error(f'{ip} : {ex}')
            continue # volver a conectarse al PLC 
        except psycopg2.Error as ex:
            logger.error(f'{ip} : {ex}')
            continue # volver a conectarse a la db
        except Exception as ex:
            logger.error(f'{ip} : {ex}') # otro error, hay que ver que paso
            raise ex
        
def file_check(path: Path) -> bool:
    """Funcion para chequear si existe el archivo de stop"""
    return path.exists()

def seconds_to_hms(seconds):
    """Funcion para convertir segundos a datetime.time"""
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return datetime.time(hour=int(hours), minute=int(minutes), second=int(seconds))

def insert_stop(conn_data: dict[str], schema: str, fecha: datetime.date, start_time: datetime.time, end_time: datetime.time, registers: int):
    """Funcion para insertar en la db"""
    try:
        with psycopg2.connect(host=conn_data["host"], dbname=conn_data["dbname"], user=conn_data["user"], password=conn_data["password"]) as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"INSERT INTO {schema}.generales (fecha, hora_inicio, hora_fin, registros) VALUES (%s, %s, %s, %s)", (fecha, start_time, end_time, registers))
                conn.commit()
    except psycopg2.Error as ex:
        raise ex
    
    return

def insert_timers(conn_data: dict[str], data: dict[str, int]):
    try:
        with psycopg2.connect(host=conn_data["host"], dbname=conn_data["dbname"], user=conn_data["user"], password=conn_data["password"]) as conn:
            with conn.cursor() as cursor:
                for puesto in data.keys():
                    cursor.execute(f"INSERT INTO {conn_data['schema']}.tiempos_parada (fecha, hora, name, tiempo) VALUES (%s, %s, %s, %s)", (datetime.datetime.now().date(), datetime.datetime.now().time(), puesto, data[puesto]))
                conn.commit()
    except psycopg2.Error as ex:
        raise ex

    return

def file_process(path: Path, conn_data: dict[str], fecha:datetime.date) -> None:
    """Funcion para procesar el archivo de stop"""
    try:
        df = pd.read_csv(path, usecols=['tiempo', 'registro'])
    except Exception as ex:
        raise ex
    
    # convertir tiempo en segundos a datetime.time
    df['tiempo'] = df['tiempo'].apply(seconds_to_hms)
    # obtener tiempo de inicio y fin
    start_time = df['tiempo'].iloc[2]   # Se toma la hora de la tercer media
    end_time = df['tiempo'].iloc[-1]
    # obtener cantidad de registros
    registers = int(df['registro'].count())
    try:
        insert_stop(conn_data, conn_data["schema"], fecha, start_time, end_time, registers)
    except Exception as ex:
        raise ex
    
    return
    
def stop_check(stop_file: Path, conn_data: dict[str], fecha: datetime.date, events: list[threading.Event]) -> None:
    """Funcion para frenar el proceso fuera de la ventana 5AM - 6PM o si aparece el archivo de stop"""
    end_time = datetime.time(18, 0)  # 6 PM

    while not stop_flag.is_set():
        current_time = datetime.datetime.now().time()

        if current_time > end_time:
            logger.warning("Terminando proceso por fuera de ventana 5AM - 6PM")
            stop_flag.set()
            #break

        try:
            if file_check(stop_file):
                logger.warning("Terminando proceso por archivo de stop")
                time.sleep(60) # esperar un minuto para que se termine de escribir el archivo
                try:    
                    file_process(stop_file, conn_data, fecha)
                except Exception as ex:
                    logger.error(f'{ex}')
                    continue
                else:
                    # setear el evento de parada para terminar los threads de lectura
                    stop_flag.set()
                    # setear los eventos para comenzar lectura de tiempos de parada
                #break
        except Exception as ex:
            logger.error(f'Error chequeando el stop file: {stop_file} : {ex}')
            continue

        time.sleep(60)
    for event in events:
        event.set()

def getCountersName(conn_data: dict[str]) -> list[tuple[str, str, str]]:
    """Funcion para obtener los nombres de los contadores de la db"""
    try:
        with psycopg2.connect(host=conn_data["host"], dbname=conn_data["dbname"], user=conn_data["user"], password=conn_data["password"]) as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"select row_to_json(j) from (SELECT ip, tag, name, orden_array FROM {conn_data['schema']}.counters_name order by orden_array asc) j")
                rst = cursor.fetchall()
                return [x[0] for x in rst] if rst else None
    except psycopg2.Error as ex:
        raise ex
    
def transformCountersName(counters_name: list[tuple[str, str, str]]) -> dict[str, dict[str, int]]:
    """Funcion para transformar los nombres de los contadores en un diccionario"""
    counters = {}
    for counter in counters_name:
        if counter['ip'] not in counters:
            counters[counter['ip']] = {}
        counters[counter['ip']][counter['tag']] = {'name': counter['name'], 'value': 0}
    return counters

def transformCountersByOrder(counters_name: list[tuple[str, str, str]]) -> dict[int, dict[str]]:
    """Funcion para transformar los nombres de los contadores en un diccionario"""
    counters = {}
    for counter in counters_name:
        if counter['ip'] not in counters:
            counters[counter['ip']] = {}
        counters[counter['ip']][int(counter['orden_array'])] = {'name': counter['name']}
    return counters

def insert_velocidad(conn, schema: str, frec: float, vel: float):
    """Funcion para insertar en la db la velocidad de la noria"""
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"INSERT INTO {schema}.velocidad (frec, vel, status_noria) VALUES (%s, %s, %s)", (frec, vel, status_noria.is_set() ))
            conn.commit()
    except psycopg2.Error as ex:
        logger.error(f'Error en query: {ex}')
        return
    return

def velocidadHandler(ip: str,conn_data : dict[str], convertion: float):
    """Funcion para leer la velocidad de la noria"""
    global velocidad

    while not stop_flag.is_set(): # ciclo para reconectarse al PLC o a la db
        try:
            with LogixDriver(ip) as plc, psycopg2.connect(host=conn_data["host"], dbname=conn_data["dbname"], user=conn_data["user"], password=conn_data["password"]) as conn:
                logger.info(f'Conectado a {ip} para leer la velocidad de la noria')
                while not stop_flag.is_set(): # ciclo para leer el status de la noria
                    value = plc.read("frec")
                    if value and len(value) > 0:
                        frec = float(value[1])
                        velocidad.set(frec * convertion)
                        insert_thread = threading.Thread(target=insert_velocidad, args=(conn, conn_data["schema"], frec, frec * convertion))
                        insert_thread.start()
                    time.sleep(0.5)
        except Exception as ex:
            logger.error(f'{ip} : {ex}')

def statusNoriaHandler(ip: str):
    """Funcion para leer el estado de la noria"""
    while not stop_flag.is_set(): # ciclo para reconectarse al PLC o a la db
        try:
            with LogixDriver(ip) as plc:
                logger.info(f'Conectado a {ip} para leer el status de la noria')
                while not stop_flag.is_set(): # ciclo para leer el status de la noria
                    value = plc.read("status")
                    if value and len(value) > 0:
                        status = value[1]
                        if status == True and not status_noria.is_set():
                            status_noria.set()
                        elif status == False and status_noria.is_set():
                            status_noria.clear()
                    time.sleep(1)
        except Exception as ex:
            logger.error(f'{ip} : {ex}')

def getCountersTime(ip: str, contadores: dict[int, dict[str]], conn_data: dict[str], event: threading.Event):
    while event.is_set(): # ciclo para reconectarse al PLC o a la db
        try:
            with LogixDriver(ip) as plc:
                logger.info(f'Conectado a {ip} para leer los tiempos de parada')
                while event.is_set(): # ciclo para leer el status de la noria
                    values = plc.read("CTimersArray{19}")
                    if not values:
                        time.sleep(1)
                        continue
                    time_by_counter = {}
                    for i, value in enumerate(values[1]):
                        if i in contadores:
                            counter_name = contadores[i]['name']
                            time_by_counter[counter_name] = value
                    insert_timers(conn_data, time_by_counter)
                    logger.info(f'{ip} : {time_by_counter}')
                    event.clear()
        except Exception as ex:
            logger.error(f'{ip} : {ex}')

def main() -> None:
    load_dotenv()   # cargar variables de entorno

    variables = ['HOST', 'DBNAME', 'SCHEMA', 'USER', 'PASSWORD', "IP_PLC_08", "IP_PLC_09", "EDU_PATH", "NORIA_CONV"]
    for variable in variables:
        if variable not in os.environ:
            raise Exception(f'Falta la variable de entorno {variable}')
    
    IP_PLC_08 = os.environ["IP_PLC_08"]
    IP_PLC_09 = os.environ["IP_PLC_09"]

    today = datetime.date.today()
    stop_file = Path(os.environ["EDU_PATH"]) / f"{today.strftime('%Y%m%d')}.CSV"

    conn_data = {
        'host': os.environ["HOST"],
        'dbname': os.environ["DBNAME"],
        'user': os.environ["USER"],
        'password': os.environ["PASSWORD"],
        'schema': os.environ["SCHEMA"]
    }

    # obtener los nombres de los contadores
    pre_counters_name = getCountersName(conn_data)
    if not pre_counters_name:
        raise Exception('No se encontraron los nombres de los contadores')
    counters_name = transformCountersName(pre_counters_name)
    counters_names_by_order = transformCountersByOrder(pre_counters_name)
    
    # armar threads, uno por cada plc y otro para controlar el tiempo
    status_noria_thread = threading.Thread(name="status_noria",target=statusNoriaHandler, args=(IP_PLC_09,))
    velocidad_thread = threading.Thread(name="velocidad_noria",target=velocidadHandler, args=(IP_PLC_09, conn_data, float(os.environ["NORIA_CONV"])))
    t_08 = threading.Thread(name=IP_PLC_08 ,target=PLCHandler, args=(conn_data, IP_PLC_08, counters_name[IP_PLC_08]))
    t_09 = threading.Thread(name=IP_PLC_09 , target=PLCHandler, args=(conn_data, IP_PLC_09, counters_name[IP_PLC_09]))
    t_stop_check = threading.Thread(name="stop_check",target=stop_check, args=(stop_file, conn_data, today, [lectura_tiempos_08, lectura_tiempos_09]))

    # iniciar threads
    status_noria_thread.start()
    velocidad_thread.start()
    t_08.start()
    t_09.start()
    t_stop_check.start()
    
    # esperar a que terminen todos antes de terminar el programa
    t_08.join()
    t_09.join()
    t_stop_check.join()
    status_noria_thread.join()
    velocidad_thread.join()

    # leer los tiempos de parada antes de terminar
    timers_08 = threading.Thread(target=getCountersTime, args=(IP_PLC_08, counters_names_by_order[IP_PLC_08], conn_data, lectura_tiempos_08))
    timers_09 = threading.Thread(target=getCountersTime, args=(IP_PLC_09, counters_names_by_order[IP_PLC_09], conn_data, lectura_tiempos_09))
    timers_08.start()
    timers_09.start()
    timers_08.join()
    timers_09.join()


if __name__ == "__main__":

    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)

    file_handler = logging.FileHandler("FaenaParadas.log")
    file_handler.setLevel(logging.INFO)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s - %(module)s - %(levelname)s - %(threadName)s - %(message)s"
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.info("Iniciando proceso")

    # flag para frenar el proceso
    stop_flag = threading.Event()

    # status de la noria
    status_noria = threading.Event()

    # definir eventos para lectura de tiempos de parada
    lectura_tiempos_08 = threading.Event()
    lectura_tiempos_09 = threading.Event()

    # velocidad de la noria
    velocidad = SharedVelocidad()

    try:
        main()
    except Exception as e:
        logger.error(e)
        raise e

