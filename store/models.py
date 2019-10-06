import uuid

from django import forms
from django.db import models
from django.db.models import Max
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
from django.forms import ModelForm
from django.utils import timezone

from store.validators import validate_csv_file_extension
from utils import aws
from utils.storage import OverWriteStorage, clear_folder

_UPDATE_INVENTORY = 'update_inventory'
_DELETE_CSV = 'delete_csv'


def upload_path(instance, filename):
    return 'csv/store_{0}/{1}'.format(instance.id, filename)


class Store(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField('Name', max_length=200)
    contact_name = models.CharField('Contact\'s name', max_length=200)
    email = models.CharField('Email', max_length=200, unique=True)
    seller_id = models.CharField('Seller ID', max_length=128)
    auth_token = models.CharField('MWS Auth Token', max_length=255)
    csv = models.FileField('File', upload_to=upload_path, validators=[validate_csv_file_extension],
                           storage=OverWriteStorage(), null=True, blank=True)
    csv_datetime = models.DateTimeField('Date Time', null=True, blank=True)
    csv_update_number = models.BigIntegerField('Update Number', null=True, blank=True)
    create_date = models.DateField('Creation date', auto_now_add=True)
    last_execution = models.DateTimeField('Last execution', null=True, blank=True)

    class Meta:
        verbose_name = 'Store'
        verbose_name_plural = 'Stores'

    def __str__(self):
        return self.name

    def save(self, *args, **kw):
        super(Store, self).save(*args, **kw)


class StoreFile(Store):
    class Meta:
        proxy = True
        verbose_name = 'Update Inventory'
        verbose_name_plural = 'Update Inventory'


@receiver(pre_save, sender=StoreFile)
def _set_csv_fields(sender, instance, *args, **kwargs):
    instance.csv_datetime = timezone.now()
    current_csv_update_number = Store.objects.filter(id=instance.id).aggregate(Max('csv_update_number'))
    max_csv_update_number = current_csv_update_number['csv_update_number__max']
    if max_csv_update_number is None:
        max_csv_update_number = 0
    instance.csv_update_number = max_csv_update_number + 1


@receiver(post_save, sender=StoreFile)
def _save_file(sender, instance, created, **kwargs):
    if hasattr(instance, _UPDATE_INVENTORY):
        with open(instance.csv.path, 'r') as csv_file:
            iter_csv_file = iter(csv_file)
            next(iter_csv_file)
            for line in iter_csv_file:
                columns = line.rstrip().split(',')
                try:
                    inventory = Inventory.objects.get(sku=columns[1], store=instance)
                except Inventory.DoesNotExist:
                    inventory = Inventory(sku=columns[1],
                                          store=instance)
                populate_inventory(columns, instance, inventory)
                inventory.save()


def normalize_condition(condition):
    if condition is None:
        return
    conditions = {
        'new': 'New',
        'usedlikenew': 'UsedLikeNew',
        'usedverygood': 'UsedVeryGood',
        'usedgood': 'UsedGood',
        'usedacceptable': 'UsedAcceptable',
        'collectiblelikenew': 'CollectibleLikeNew',
        'collectibleverygood': 'CollectibleVeryGood',
        'collectiblegood': 'CollectibleGood',
        'collectibleacceptable': 'CollectibleAcceptable',
        'refurbished': 'Refurbished',
        'club': 'Club'
    }
    return conditions[condition.replace(' ', '').lower()]


def populate_inventory(columns, instance, inventory):
    inventory.upc = columns[0]
    inventory.sku_vendor = columns[2]
    inventory.cost_price = columns[3]
    inventory.drop_fee = columns[4]
    inventory.shipment_price = columns[5]
    inventory.standard_price = columns[6]
    inventory.quantity = columns[7]
    inventory.condition = normalize_condition(columns[8])
    inventory.handling_time = columns[9]
    inventory.wholesale_name = columns[10]
    inventory.csv_filename = str(instance.csv).rsplit('/', 1)[1]
    inventory.csv_datetime = instance.csv_datetime
    inventory.csv_update_number = instance.csv_update_number
    product = aws.get_items(instance.seller_id, instance.auth_token, [inventory.upc])
    if product.parsed:
        if product.parsed['status'] and product.parsed['status']['value'] == 'ClientError':
            print(product.parsed['Error']['Message']['value'])
        elif product.parsed['Products'] and product.parsed['Products']['Product']:
            product_parsed = product.parsed['Products']['Product']
            if product_parsed['Identifiers'] and product_parsed['Identifiers']['MarketplaceASIN'] and \
                    product_parsed['Identifiers']['MarketplaceASIN']['ASIN']:
                asin = product_parsed['Identifiers']['MarketplaceASIN']['ASIN']
                inventory.asin = asin['value']
            if product_parsed['AttributeSets'] and product_parsed['AttributeSets']['ItemAttributes'] and \
                    product_parsed['AttributeSets']['ItemAttributes']['Title']:
                title = product_parsed['AttributeSets']['ItemAttributes']['Title']
                inventory.item_name = title['value']


@receiver(post_delete, sender=Store)
def _store_delete(sender, instance, **kwargs):
    clear_folder(instance.csv.path, True)


class StoreForm(ModelForm):
    class Meta:
        model = Store
        exclude = ['last_execution']


def inventory_form_factory(request, obj):
    class InventoryForm(ModelForm):
        store = forms.CharField(
            widget=forms.TextInput(attrs={'readonly': 'readonly'})
        )

        class Meta:
            model = StoreFile
            fields = ['store', 'csv', ]
            widgets = {
                'csv': forms.FileInput()
            }

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields['csv'].required = True
            if obj:
                self.fields['store'].initial = obj.name
                self.fields['store'].disabled = True

        def clean_csv(self):
            old_csv = self.instance.csv
            cleaned_csv = self.cleaned_data["csv"]
            if 'csv' in self.changed_data:
                setattr(self.instance, _UPDATE_INVENTORY, 1)
                if not cleaned_csv:
                    setattr(self.instance, _DELETE_CSV, old_csv)
            return cleaned_csv
    return InventoryForm


class Inventory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upc = models.CharField('UPC', max_length=200)
    asin = models.CharField('ASIN', max_length=200, null=True, blank=True)
    item_name = models.CharField('Item Name', max_length=200, null=True, blank=True)
    sku = models.CharField('SKU', max_length=200)
    sku_vendor = models.CharField('SKU Vendor', max_length=200)
    cost_price = models.DecimalField('Cost Price', max_digits=12, decimal_places=2)
    drop_fee = models.DecimalField('Drop Fee', max_digits=12, decimal_places=2)
    shipment_price = models.DecimalField('Shipment Price', max_digits=12, decimal_places=2)
    standard_price = models.DecimalField('Standard Price', max_digits=12, decimal_places=2)
    quantity = models.IntegerField('Quantity')
    condition = models.CharField('Condition', max_length=200)
    handling_time = models.IntegerField('Handling Time')
    wholesale_name = models.CharField('Wholesale Name', max_length=200)
    is_synced = models.BooleanField('Synced', default=False)
    create_date = models.DateField('Creation date', auto_now_add=True)
    csv_filename = models.CharField('Filename', max_length=200, null=True, blank=True)
    csv_datetime = models.DateTimeField('Date Time', null=True, blank=True)
    csv_update_number = models.BigIntegerField('Update Number', null=True, blank=True)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, blank=True, null=True)

    class Meta:
        verbose_name = 'Inventory'
        verbose_name_plural = 'Inventory'
        permissions = (
            ('sync_inventory', 'Can sync permission'),
        )

    def __str__(self):
        return self.sku


class FeedSubmissionInfo(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    feed_submission_id = models.CharField('Feed Submission ID', max_length=200)
    feed_type = models.CharField('Feed Type', max_length=200)
    submitted_date = models.DateTimeField('Submitted Date')
    feed_processing_status = models.CharField('Feed Processing Status', max_length=200)
    started_processing_date = models.DateTimeField('Start Processing Date', blank=True, null=True)
    completed_processing_date = models.DateTimeField('Start Processing Date', blank=True, null=True)
    store = models.ForeignKey(Store, on_delete=models.CASCADE)
