import uuid

from django.db import models
from django.contrib.auth.models import (
    BaseUserManager, AbstractUser)

from store.models import Store


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, username, email, password, store=None, **extra_fields):
        if not username:
            raise ValueError('The given username must be set')
        email = self.normalize_email(email)
        username = self.model.normalize_username(username)
        user = self.model(username=username, email=email, store=store, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, username, email=None, store=None, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        return self._create_user(username, email, store, password, **extra_fields)

    def create_superuser(self, username, email, password, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self._create_user(username, email, password, **extra_fields)


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.SET_NULL, blank=True, null=True)

    objects = UserManager()

    REQUIRED_FIELDS = ['email']
    EMAIL_FIELD = 'email'
    USERNAME_FIELD = 'username'

    class Meta():
        verbose_name = 'User'
        verbose_name_plural = 'Users'
