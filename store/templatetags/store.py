from django import template
from django.urls import reverse

register = template.Library()


@register.simple_tag
def get_user_change_url(user, **kwargs):
    model_object_name = kwargs['model']['object_name'].lower()
    if user.is_superuser or model_object_name == 'inventory' or model_object_name == 'feedsubmissioninfo':
        return kwargs['model']['admin_url']
    elif user.store:
        return reverse('admin:%s_%s_change' % (kwargs['app']['app_label'],
                                               kwargs['model']['object_name'].lower()),
                       args=(user.store.id,),
                       current_app='admin')
    return None


@register.filter
def has_perm(user, perm):
    if user.is_superuser or user.store:
        return user.has_perm(perm)
    return False
