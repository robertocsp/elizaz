# Generated by Django 2.2.5 on 2019-09-20 19:47

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0006_auto_20190920_0143'),
    ]

    operations = [
        migrations.AlterField(
            model_name='inventory',
            name='sku',
            field=models.CharField(max_length=200, verbose_name='SKU'),
        ),
    ]
