from __future__ import annotations

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter


class KeycloakSocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin) -> bool:
        return True

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        extra = sociallogin.account.extra_data
        if not user.first_name and extra.get("given_name"):
            user.first_name = extra["given_name"]
        if not user.last_name and extra.get("family_name"):
            user.last_name = extra["family_name"]
        return user
