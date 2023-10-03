from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core import mail
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

CustomUser = get_user_model()


class UserCreationTests(APITestCase):
    def setUp(self):
        self.admin = CustomUser.objects.create_superuser(
            email="admin@example.com",
            password="AdminPass!123",
        )

    def test_admin_creates_user_with_secure_temporary_password(self):
        """
        Ensure that when an admin creates a user via the API, the user is
        marked as first login and does not store the raw password value.
        """
        self.client.force_authenticate(user=self.admin)
        url = reverse("user-list-create")
        payload = {
            "email": "newuser@example.com",
            "first_name": "New",
            "last_name": "User",
            "user_type": "client",
        }

        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created_user = CustomUser.objects.get(email="newuser@example.com")
        self.assertTrue(created_user.is_first_login)
        self.assertTrue(created_user.is_active)
        # The password field on the model must be a hashed value and not empty.
        self.assertNotEqual(created_user.password, "")
        self.assertNotIn("password", response.data)


class FirstTimePasswordUpdateTests(APITestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="user@example.com",
            password="TempPass!123",
            is_first_login=True,
        )
        self.other_user = CustomUser.objects.create_user(
            email="other@example.com",
            password="OtherPass!123",
            is_first_login=True,
        )

    def test_user_can_update_own_password_on_first_login(self):
        self.client.force_authenticate(user=self.user)
        url = reverse("update-password", kwargs={"pk": self.user.pk})
        payload = {
            "new_password": "NewSecurePass!123",
            "confirm_password": "NewSecurePass!123",
        }

        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.user.refresh_from_db()
        self.assertFalse(self.user.is_first_login)
        self.assertTrue(self.user.check_password("NewSecurePass!123"))

    def test_user_cannot_update_other_users_password(self):
        self.client.force_authenticate(user=self.other_user)
        url = reverse("update-password", kwargs={"pk": self.user.pk})
        payload = {
            "new_password": "NewSecurePass!123",
            "confirm_password": "NewSecurePass!123",
        }

        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CustomerAccessControlTests(APITestCase):
    def setUp(self):
        self.admin = CustomUser.objects.create_superuser(
            email="admin2@example.com",
            password="AdminPass!123",
        )
        self.client_user = CustomUser.objects.create_user(
            email="client@example.com",
            password="ClientPass!123",
            user_type="client",
            is_first_login=False,
        )
        self.other_client_user = CustomUser.objects.create_user(
            email="other-client@example.com",
            password="ClientPass!123",
            user_type="client",
            is_first_login=False,
        )
        self.customer = CustomUser.objects.create_user(
            email="customer@example.com",
            password="CustomerPass!123",
            user_type="customer",
            client=self.client_user,
            is_first_login=False,
        )

    def test_admin_can_access_any_customer(self):
        self.client.force_authenticate(user=self.admin)
        url = reverse(
            "customer-detail",
            kwargs={"client_id": self.client_user.pk, "pk": self.customer.pk},
        )

        response = self.client.get(url, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], self.customer.email)

    def test_client_cannot_access_customer_of_another_client(self):
        self.client.force_authenticate(user=self.other_client_user)
        url = reverse(
            "customer-detail",
            kwargs={"client_id": self.client_user.pk, "pk": self.customer.pk},
        )

        response = self.client.get(url, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class PasswordResetTests(APITestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="reset@example.com", password="ResetPass!123"
        )
        self.url = reverse("password-reset")

    def test_the_reset_token_is_not_returned_in_the_response(self):
        # The endpoint used to hand the caller a working reset link. Anyone
        # who could POST an email address could take over that account.
        response = self.client.post(self.url, {"email": self.user.email}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("reset_link", response.data)
        body = str(response.data)
        self.assertNotIn("/reset/", body)

    def test_the_link_is_emailed_and_points_at_the_configured_frontend(self):
        self.client.post(self.url, {"email": self.user.email}, format="json")

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn(settings.PASSWORD_RESET_URL_BASE, message.body)
        self.assertIn("/reset/", message.body)
        self.assertNotIn(
            "example.com/reset", message.body.replace(settings.PASSWORD_RESET_URL_BASE, "", 1)
        )
        self.assertEqual(message.from_email, settings.DEFAULT_FROM_EMAIL)
        self.assertEqual(message.to, [self.user.email])

    def test_an_unknown_address_is_rejected_without_sending_mail(self):
        response = self.client.post(self.url, {"email": "nobody@example.com"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_malformed_address_is_rejected(self):
        response = self.client.post(self.url, {"email": "not-an-email"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class LoginEndpointTests(APITestCase):
    """
    /account/login/ pointed at django.contrib.auth.views.LoginView, which
    renders registration/login.html -- a template this project does not ship.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="login@example.com",
            password="LoginPass!123",
            user_type="client",
            is_first_login=False,
        )
        self.url = reverse("login")

    def test_valid_credentials_are_accepted(self):
        response = self.client.post(
            self.url,
            {"email": self.user.email, "password": "LoginPass!123"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["message"], "Logged in successfully")

    def test_a_wrong_password_is_rejected(self):
        response = self.client.post(
            self.url, {"email": self.user.email, "password": "wrong"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_unknown_email_is_rejected(self):
        response = self.client.post(
            self.url, {"email": "ghost@example.com", "password": "x"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_deactivated_account_cannot_log_in(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self.client.post(
            self.url,
            {"email": self.user.email, "password": "LoginPass!123"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_first_login_is_told_to_change_its_password(self):
        self.user.is_first_login = True
        self.user.save(update_fields=["is_first_login"])
        response = self.client.post(
            self.url,
            {"email": self.user.email, "password": "LoginPass!123"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["message"], "First login, password update required")


class EmailBackendTests(APITestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="backend@example.com", password="BackendPass!123"
        )

    def test_authenticate_resolves_a_user_by_email(self):
        self.assertEqual(
            authenticate(username="backend@example.com", password="BackendPass!123"),
            self.user,
        )

    def test_authenticate_rejects_a_wrong_password(self):
        self.assertIsNone(authenticate(username="backend@example.com", password="nope"))

    def test_authenticate_rejects_an_unknown_email(self):
        self.assertIsNone(authenticate(username="ghost@example.com", password="x"))


class CustomerCreateTests(APITestCase):
    def setUp(self):
        self.client_user = CustomUser.objects.create_user(
            email="create-client@example.com",
            password="ClientPass!123",
            user_type="client",
            is_first_login=False,
        )
        self.admin = CustomUser.objects.create_superuser(
            email="create-admin@example.com", password="AdminPass!123"
        )
        self.url = reverse("customer-create", kwargs={"client_id": self.client_user.pk})

    def test_a_form_encoded_post_is_accepted(self):
        # request.data is an immutable QueryDict for form posts; the view used
        # to assign into it, which raises "This QueryDict instance is immutable".
        self.client.force_authenticate(user=self.client_user)
        response = self.client.post(
            self.url,
            {"email": "cust1@example.com", "user_type": "customer"},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = CustomUser.objects.get(email="cust1@example.com")
        self.assertEqual(created.client, self.client_user)

    def test_a_client_cannot_attach_a_customer_to_someone_else(self):
        other = CustomUser.objects.create_user(
            email="other-client2@example.com", password="Pass!12345", user_type="client"
        )
        url = reverse("customer-create", kwargs={"client_id": other.pk})
        self.client.force_authenticate(user=self.client_user)
        response = self.client.post(
            url, {"email": "cust2@example.com", "user_type": "customer"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(CustomUser.objects.get(email="cust2@example.com").client, self.client_user)

    def test_an_admin_attaches_the_customer_to_the_named_client(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            self.url,
            {"email": "cust3@example.com", "user_type": "customer"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(CustomUser.objects.get(email="cust3@example.com").client, self.client_user)


class UserManagerTests(APITestCase):
    def test_an_email_is_required(self):
        with self.assertRaises(ValueError):
            CustomUser.objects.create_user(email="", password="x")

    def test_the_email_is_normalised(self):
        user = CustomUser.objects.create_user(email="Mixed@EXAMPLE.COM", password="Pass!12345")
        self.assertEqual(user.email, "Mixed@example.com")

    def test_a_superuser_is_staff_and_superuser(self):
        user = CustomUser.objects.create_superuser(email="su@example.com", password="Pass!12345")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_a_superuser_cannot_be_created_without_staff(self):
        with self.assertRaises(ValueError):
            CustomUser.objects.create_superuser(
                email="su2@example.com", password="Pass!12345", is_staff=False
            )


class PasswordChangeTests(APITestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="change@example.com", password="OldPass!12345", is_first_login=False
        )
        self.client.force_authenticate(user=self.user)
        self.url = reverse("password-change")

    def test_a_valid_change_is_applied(self):
        response = self.client.post(
            self.url,
            {
                "old_password": "OldPass!12345",
                "new_password": "BrandNewPass!456",
                "confirm_new_password": "BrandNewPass!456",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("BrandNewPass!456"))

    def test_the_old_password_must_be_correct(self):
        response = self.client.post(
            self.url,
            {
                "old_password": "WrongPass!123",
                "new_password": "BrandNewPass!456",
                "confirm_new_password": "BrandNewPass!456",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("old_password", response.data)

    def test_the_two_new_passwords_must_match(self):
        response = self.client.post(
            self.url,
            {
                "old_password": "OldPass!12345",
                "new_password": "BrandNewPass!456",
                "confirm_new_password": "SomethingElse!789",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_weak_new_password_is_refused(self):
        response = self.client.post(
            self.url,
            {
                "old_password": "OldPass!12345",
                "new_password": "12345678",
                "confirm_new_password": "12345678",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class AdminOnlyEndpointTests(APITestCase):
    def setUp(self):
        self.member = CustomUser.objects.create_user(
            email="member@example.com", password="Pass!12345", user_type="client"
        )

    def test_the_user_list_is_closed_to_non_staff(self):
        self.client.force_authenticate(user=self.member)
        self.assertEqual(
            self.client.get(reverse("user-list-create")).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_the_client_list_is_closed_to_non_staff(self):
        self.client.force_authenticate(user=self.member)
        self.assertEqual(
            self.client.get(reverse("client-list-create")).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_the_profile_endpoint_returns_the_caller(self):
        self.client.force_authenticate(user=self.member)
        response = self.client.get(reverse("user-profile"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], self.member.email)
