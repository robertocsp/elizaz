# Generated by Django 2.2.5 on 2019-09-18 02:52

from django.db import migrations, models
import store.models
import store.validators
import utils.storage
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Store',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=200, verbose_name='Name')),
                ('contact_name', models.CharField(max_length=200, verbose_name="Contact's name")),
                ('email', models.CharField(max_length=200, unique=True, verbose_name='Email')),
                ('seller_id', models.CharField(max_length=128, verbose_name='Seller ID')),
                ('auth_token', models.CharField(max_length=255, verbose_name='MWS Auth Token')),
                ('csv', models.FileField(blank=True, null=True, storage=utils.storage.OverWriteStorage(), upload_to=store.models.upload_path, validators=[store.validators.validate_csv_file_extension], verbose_name='File')),
                ('create_date', models.DateField(auto_now_add=True, verbose_name='Creation date')),
            ],
            options={
                'verbose_name': 'Store',
                'verbose_name_plural': 'Stores',
            },
        ),
        migrations.CreateModel(
            name='StoreFile',
            fields=[
            ],
            options={
                'verbose_name': 'Update Inventory',
                'verbose_name_plural': 'Update Inventory',
                'proxy': True,
                'indexes': [],
                'constraints': [],
            },
            bases=('store.store',),
        ),
    ]
