"""
Account use cases.

The password-reset flow in particular is worth having out of the view: it is
the piece of this codebase with a security history (the reset token used to be
returned in the HTTP response, which is an unauthenticated account takeover),
and it is easier to keep right when it is one function.
"""

from __future__ import annotations

import logging
from typing import Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

logger = logging.getLogger(__name__)

CustomUser = get_user_model()

AUTH_BACKEND = "accounts.backends.EmailBackend"


def send_password_reset(email: str) -> bool:
    """
    Email a reset link. Returns False when no such account exists.

    The token goes in the mail and nowhere else - never into the response.
    """
    user = CustomUser.objects.filter(email=email).first()
    if user is None:
        return False

    token = default_token_generator.make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    reset_link = f"{settings.PASSWORD_RESET_URL_BASE.rstrip('/')}/reset/{uid}/{token}/"

    send_mail(
        "Password Reset",
        f"Use this link to reset your password: {reset_link}",
        settings.DEFAULT_FROM_EMAIL,
        [email],
    )
    return True


def authenticate_by_email(*, email: str, password: str) -> Optional[CustomUser]:
    """
    Resolve credentials to an active user.

    ``is_active`` is checked here: without it a deactivated account could
    still log in.
    """
    user = CustomUser.objects.filter(email=email).first()
    if user and user.is_active and user.check_password(password):
        return user
    return None


def needs_password_change(user) -> bool:
    return bool(user.is_first_login and user.user_type != "admin")


def customers_for(requesting_user, client_id: Optional[int] = None):
    """The customers one caller is allowed to see."""
    customers = CustomUser.objects.filter(user_type="customer").select_related("client")
    if requesting_user.user_type == "admin":
        if client_id is not None:
            return customers.filter(client_id=client_id).order_by("id")
        # Admin sees every customer that is actually attached to a client.
        return customers.exclude(client__isnull=True).order_by("id")
    return customers.filter(client=requesting_user).order_by("id")
