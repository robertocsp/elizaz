import shutil

from django.core.files.storage import FileSystemStorage
from django.conf import settings
import os


class OverWriteStorage(FileSystemStorage):
    def get_available_name(self, name, max_length=None):
        clear_folder(name)
        return name


def clear_folder(relative_path, purge=False):
    dir_name, file_name = os.path.split(relative_path)
    if os.path.exists(os.path.join(settings.MEDIA_ROOT, dir_name)):
        if purge:
            shutil.rmtree(os.path.join(settings.MEDIA_ROOT, dir_name))
        else:
            for root, dirs, files in os.walk(os.path.join(settings.MEDIA_ROOT, dir_name)):
                for file in files:
                    os.remove(os.path.join(root, file))
