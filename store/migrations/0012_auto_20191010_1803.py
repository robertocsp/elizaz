# Generated by Django 2.2.5 on 2019-10-10 21:03

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0011_auto_20191002_1048'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='inventory',
            name='is_synced',
        ),
        migrations.AddField(
            model_name='inventory',
            name='sync_status',
            field=models.SmallIntegerField(choices=[(0, 'Not synced'), (1, 'Synced'), (2, 'Awaiting check sync status')], default=0, verbose_name='Sync Status'),
        ),
    ]