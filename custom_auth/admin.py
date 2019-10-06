from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as ParentClassUserAdmin
from django.contrib.auth.forms import UserChangeForm as ParentClassUserChangeForm, \
    UserCreationForm as ParentClassUserCreationForm

from custom_auth.models import User
from utils.thread_local import get_current_user


class UserChangeForm(ParentClassUserChangeForm):
    class Meta(ParentClassUserChangeForm.Meta):
        model = User

    def __init__(self, *args, **kwargs):
        super(UserChangeForm, self).__init__(*args, **kwargs)
        if get_current_user().is_superuser is False:
            self.fields['store'].required = True


class UserCreationForm(ParentClassUserCreationForm):
    class Meta(ParentClassUserCreationForm.Meta):
        model = User

    def __init__(self, *args, **kwargs):
        super(UserCreationForm, self).__init__(*args, **kwargs)
        if get_current_user().is_superuser is False:
            self.fields['store'].required = True


class UserAdmin(ParentClassUserAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    fieldsets = (
        (None, {'fields': ('username', 'password', 'store')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'email')}),
        ('Permissions', {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
        }),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'password1', 'password2', 'store',),
        }),
    )


admin.site.register(User, UserAdmin)
