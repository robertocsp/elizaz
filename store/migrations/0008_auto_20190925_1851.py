# Generated by Django 2.2.5 on 2019-09-25 21:51

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0007_auto_20190920_1647'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='inventory',
            options={'permissions': (('sync_inventory', 'Can sync permission'),), 'verbose_name': 'Inventory', 'verbose_name_plural': 'Inventory'},
        ),
    ]
