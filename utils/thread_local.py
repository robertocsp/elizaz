from django.http import HttpRequest
from django.utils.deprecation import MiddlewareMixin
from threading import local

_thread_local = local()


def get_current_request():
    request = getattr(_thread_local, 'request', None)
    if request is None:
        request = HttpRequest()
    return request


def get_current_user():
    request = get_current_request()
    if request:
        return getattr(request, 'user', None)


class ThreadLocalMiddleware(MiddlewareMixin):
    @staticmethod
    def process_request(request):
        _thread_local.request = request

    @staticmethod
    def process_response(request, response):
        if hasattr(_thread_local, 'request'):
            del _thread_local.request
        return response
