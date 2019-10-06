from django import forms
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.contrib.admin.views.main import ChangeList
from django.contrib.auth import get_permission_codename
from django.db import router
from django.forms import models
from django.template.response import TemplateResponse
from django.urls import reverse, NoReverseMatch
from django.contrib.admin.utils import (
    quote,
    model_ngettext, NestedObjects)
from django.utils.decorators import method_decorator
from django.utils.html import format_html
from django.views.decorators.csrf import csrf_protect

from store.models import Store, StoreForm, StoreFile, inventory_form_factory, Inventory, FeedSubmissionInfo
from utils.aws import update_store, ThrottlingException, get_feed_submission_list
from utils.thread_local import get_current_user


csrf_protect_m = method_decorator(csrf_protect)


class StoreAdmin(admin.ModelAdmin):
    form = StoreForm
    readonly_fields = ('csv', 'csv_datetime', 'csv_update_number')

    def has_add_permission(self, request):
        if request.user.is_superuser:
            return super(StoreAdmin, self).has_add_permission(request)
        return False

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return super(StoreAdmin, self).has_delete_permission(request, obj)
        return False

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser or request.user.store:
            return super(StoreAdmin, self).has_change_permission(request, obj)
        return False

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser or request.user.store:
            return super(StoreAdmin, self).has_view_permission(request, obj)
        return False

    def get_queryset(self, request):
        qs = super(StoreAdmin, self).get_queryset(request)
        if not request.user.is_superuser:
            if request.user.store:
                qs = qs.filter(id=request.user.store.id)
            else:
                qs = qs.filter(id=None)
        return qs


admin.site.register(Store, StoreAdmin)


class UpdateInventoryAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser or request.user.store:
            return super(UpdateInventoryAdmin, self).has_change_permission(request, obj)
        return False

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser or request.user.store:
            return super(UpdateInventoryAdmin, self).has_view_permission(request, obj)
        return False

    def get_changelist(self, request, **kwargs):
        return UpdateInventoryChangeList

    def get_form(self, request, obj=None, change=False, **kwargs):
        self.form = inventory_form_factory(request, obj)
        return super(UpdateInventoryAdmin, self).get_form(request, obj, change, **kwargs)

    def get_queryset(self, request):
        qs = super(UpdateInventoryAdmin, self).get_queryset(request)
        if not request.user.is_superuser:
            if request.user.store:
                qs = qs.filter(id=request.user.store.id)
            else:
                qs = qs.filter(id=None)
        return qs


class UpdateInventoryChangeList(ChangeList):
    def url_for_result(self, result):
        pk = getattr(result, self.pk_attname)
        return reverse('admin:%s_%s_change' % (self.opts.app_label,
                                               self.opts.model_name),
                       args=(quote(pk),),
                       current_app=self.model_admin.admin_site.name)


admin.site.register(StoreFile, UpdateInventoryAdmin)


class InventoryCreationForm(models.ModelForm):
    class Meta:
        model = Inventory
        exclude = []

    def __init__(self, *args, **kwargs):
        super(InventoryCreationForm, self).__init__(*args, **kwargs)
        try:
            self.fields['store'].required = True
        except KeyError:
            pass


class InventoryChangeForm(models.ModelForm):
    class Meta:
        model = Inventory
        exclude = []

    def __init__(self, *args, **kwargs):
        super(InventoryChangeForm, self).__init__(*args, **kwargs)
        try:
            self.fields['store'].required = True
        except KeyError:
            pass


class ActionForm(forms.Form):
    action = forms.ChoiceField(label='Action:')
    action.widget.attrs['id'] = 'action-selection'
    select_across = forms.BooleanField(
        label='',
        required=False,
        initial=0,
        widget=forms.HiddenInput({'class': 'select-across'}),
    )
    # action = forms.CharField(widget=forms.HiddenInput,
    #                          initial='delete_selected',
    #                          label='Delete Selected'
    #                          )
    # select_across = forms.BooleanField(
    #                                    label='',
    #                                    required=False,
    #                                    initial=0,
    #                                    widget=forms.HiddenInput({'class': 'select-across'}),
    #                                    )


def update_aws(modeladmin, request, queryset):
    if request.method == 'POST':
        n = queryset.count()
        if n:
            objects = set()
            throttlings = set()
            for obj in queryset.order_by('store'):
                # obj_display = str(obj)
                if len(objects):
                    previous_obj = objects.pop()
                    objects.add(previous_obj)
                    if previous_obj.store.id != obj.store.id:
                        call_mws(objects, previous_obj, throttlings)
                        objects.clear()
                objects.add(obj)
            previous_obj = objects.pop()
            objects.add(previous_obj)
            call_mws(objects, previous_obj, throttlings)
            # modeladmin.log_deletion(request, obj, obj_display)
            if len(throttlings):
                for throttling in throttlings:
                    modeladmin.message_user(request, str(throttling), messages.WARNING)
            else:
                modeladmin.message_user(request, 'Successfully synced %(count)d %(items)s.' % {
                    "count": n, "items": model_ngettext(modeladmin.opts, n)
                }, messages.SUCCESS)
    # Return None to display the change list page again.
    return None


def call_mws(objects, previous_obj, throttling):
    try:
        store = Store.objects.get(seller_id=previous_obj.store.seller_id)
        store.last_execution, product_return, price_return, inventory_return = update_store(store.seller_id,
                                                                                            store.auth_token, objects,
                                                                                            store.last_execution,
                                                                                            store.name)
        store.save()
        save_return(product_return['FeedSubmissionInfo'], store)
        save_return(price_return['FeedSubmissionInfo'], store)
        save_return(inventory_return['FeedSubmissionInfo'], store)
    except ThrottlingException as e:
        throttling.add(e)
    # TODO troca SYNC para TRUE somente quando estiver no estado de DONE E SEM ERROS!!!


def save_return(feed_submission_info, store):
    feed_submission_id = feed_submission_info['FeedSubmissionId']['value']
    feed_type = feed_submission_info['FeedType']['value']
    submitted_date = feed_submission_info['SubmittedDate']['value']
    feed_processing_status = feed_submission_info['FeedProcessingStatus']['value']
    feed_info = FeedSubmissionInfo(feed_submission_id=feed_submission_id,
                                   feed_type=feed_type,
                                   submitted_date=submitted_date,
                                   feed_processing_status=feed_processing_status,
                                   store=store)
    feed_info.save()


def get_synced_objects(modeladmin, objs, request, admin_site):
    """
    Find all objects related to ``objs`` that should also be deleted. ``objs``
    must be a homogeneous iterable of objects (e.g. a QuerySet).

    Return a nested list of strings suitable for display in the
    template with the ``unordered_list`` filter.
    """
    try:
        objs[0]
    except IndexError:
        return [], {}, set(), []
    else:
        using = router.db_for_write(Inventory)
    collector = NestedObjects(using=using)
    collector.collect(objs)
    perms_needed = set()

    def format_callback(obj):
        no_edit_link = 'Inventory: %s' % (obj,)

        if not modeladmin.has_sync_permission(request, obj):
            perms_needed.add('Inventory')
        try:
            admin_url = reverse('%s:store_inventory_change'
                                % (admin_site.name,),
                                None, (quote(obj.pk),))
        except NoReverseMatch:
            # Change url doesn't exist -- don't display link to edit
            return no_edit_link

        # Display a link to the admin page.
        return format_html('Inventory: <a href="{}">{}</a>',
                           admin_url,
                           obj)

    to_sync = collector.nested(format_callback)

    protected = [format_callback(obj) for obj in collector.protected]
    model_count = {'Inventory': len(objs) for model, objs in collector.model_objs.items()}

    return to_sync, model_count, perms_needed, protected


class InventoryAdmin(admin.ModelAdmin):
    add_form = InventoryCreationForm
    change_form = InventoryChangeForm
    list_display = ('is_synced', 'upc', '_asin', '_item_name', 'sku', 'sku_vendor', 'cost_price', 'drop_fee',
                    'shipment_price', 'standard_price', 'quantity', 'condition', 'handling_time', 'wholesale_name',
                    'csv_update_number', 'csv_datetime', 'csv_filename',)
    list_display_links = ('upc',)
    readonly_fields = ('is_synced', 'csv_filename', 'csv_datetime', 'csv_update_number')
    fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('is_synced', 'store', 'upc', 'sku', 'sku_vendor', 'cost_price', 'drop_fee', 'shipment_price',
                       'standard_price', 'quantity', 'condition', 'handling_time', 'wholesale_name', 'csv_filename',
                       'csv_datetime', 'csv_update_number'),
        }),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('store', 'upc', 'sku', 'sku_vendor', 'cost_price', 'drop_fee', 'shipment_price',
                       'standard_price', 'quantity', 'condition', 'handling_time', 'wholesale_name',),
        }),
    )
    list_filter = ('is_synced', 'csv_update_number',)
    action_form = ActionForm
    actions = ['sync_inventory', 'check_sync_status']
    list_per_page = 1000

    def _asin(self, obj):
        return '-' if obj.asin is None else obj.asin

    def _item_name(self, obj):
        return '-' if obj.item_name is None else obj.item_name

    def check_sync_status(self, request, queryset):
        if request.user.is_superuser:
            store = request.POST.get('check_store_action')
        else:
            store = request.user.store
        if not store and request.POST.get('post'):
            self.message_user(request, 'Store must be selected in order to perform action on it.', messages.WARNING)
        elif store and request.POST.get('post'):
            store = Store.objects.get()
            feed_submission_info_list = FeedSubmissionInfo.objects.filter(store=store).order_by('-submitted_date')[:3]
            feed_submission_list, ended_with_error, did_not_complete = get_feed_submission_list(
                store.seller_id,
                store.auth_token,
                [feed_submission_info.feed_submission_id for feed_submission_info in feed_submission_info_list])
            for feed_submission in feed_submission_list:
                feed_submission_info = FeedSubmissionInfo.objects.get(feed_submission_id=
                                                                      feed_submission['FeedSubmissionId']['value'])
                feed_submission_info.feed_processing_status = feed_submission['FeedProcessingStatus']['value']
                feed_submission_info.started_processing_date = feed_submission['StartedProcessingDate']['value']
                feed_submission_info.completed_processing_date = feed_submission['CompletedProcessingDate']['value']
                feed_submission_info.save()
            if len(ended_with_error) > 0:
                self.message_user(request, 'The following feeds ended up with errors: %(feeds)s. Please check the logs '
                                           'for more information.' %
                                  {'feeds': ', '.join(ended_with_error)}, messages.WARNING)
            if len(did_not_complete) > 0:
                self.message_user(request, 'The following feeds did not complete yet: %(feeds)s. Please check the logs '
                                           'for more information.' %
                                  {'feeds': ', '.join(did_not_complete)}, messages.WARNING)
            if len(ended_with_error) == 0 and len(did_not_complete) == 0:
                self.message_user(request, 'All feeds completed without any errors or warnings.', messages.SUCCESS)
            return None

        context = {
            **self.admin_site.each_context(request),
            'title': 'Store selection',
            'stores': Store.objects.all() if request.user.is_superuser else Store.objects.none(),
            'media': self.media,
        }

        request.current_app = self.admin_site.name

        return TemplateResponse(request, "admin/store/inventory/select_store.html", context)

    check_sync_status.short_description = "Check Sync Status"
    check_sync_status.allowed_permissions = ('sync',)

    def sync_inventory(self, request, queryset):
        if request.POST.get('post'):
            update_aws(self, request, queryset)
            return None
        syncable_objects, model_count, perms_needed, protected = get_synced_objects(self, queryset, request,
                                                                                    self.admin_site)

        objects_name = model_ngettext(queryset)

        context = {
            **self.admin_site.each_context(request),
            'title': 'Are you sure?',
            'objects_name': str(objects_name),
            'syncable_objects': [syncable_objects],
            'model_count': dict(model_count).items(),
            'queryset': queryset,
            'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME,
            'media': self.media,
        }

        request.current_app = self.admin_site.name

        return TemplateResponse(request, "admin/store/inventory/sync_selected_confirmation.html", context)

    sync_inventory.short_description = "Sync Inventory -> Marketplace"
    sync_inventory.allowed_permissions = ('sync',)

    def has_sync_permission(self, request, obj=None):
        if request.user.is_superuser or request.user.store:
            opts = self.opts
            codename = get_permission_codename('sync', opts)
            return request.user.has_perm('%s.%s' % (opts.app_label, codename))
        return False

    def has_add_permission(self, request):
        if request.user.is_superuser or request.user.store:
            return super(InventoryAdmin, self).has_add_permission(request)
        return False

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser or request.user.store:
            return super(InventoryAdmin, self).has_delete_permission(request, obj)
        return False

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser or request.user.store:
            return super(InventoryAdmin, self).has_change_permission(request, obj)
        return False

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser or request.user.store:
            return super(InventoryAdmin, self).has_view_permission(request, obj)
        return False

    def get_readonly_fields(self, request, obj=None):
        page_readonly_fields = super(InventoryAdmin, self).get_readonly_fields(request, obj)
        if not request.user.is_superuser:
            page_readonly_fields += ('store',)
        return page_readonly_fields

    def get_queryset(self, request):
        qs = super(InventoryAdmin, self).get_queryset(request)
        if not request.user.is_superuser:
            if request.user.store:
                qs = qs.filter(store_id=request.user.store.id)
            else:
                qs = qs.filter(store_id=None)
        return qs

    def get_list_display(self, request):
        my_list_display = super(InventoryAdmin, self).get_list_display(request)
        if request.user.is_superuser:
            my_list_display = ('store',) + my_list_display
        return my_list_display

    def get_list_filter(self, request):
        my_list_filter = super(InventoryAdmin, self).get_list_filter(request)
        if request.user.is_superuser:
            my_list_filter = ('store',) + my_list_filter
        return my_list_filter

    def get_fieldsets(self, request, obj=None):
        if obj:
            return super(InventoryAdmin, self).get_fieldsets(request, obj)
        return self.add_fieldsets

    def get_form(self, request, obj=None, change=False, **kwargs):
        if obj is None:
            self.form = self.add_form
        else:
            self.form = self.change_form
        my_form = super(InventoryAdmin, self).get_form(request, obj, change, **kwargs)
        return my_form

    def get_empty_value_display(self):
        if type(self.form) == InventoryCreationForm.__class__:
            return get_current_user().store
        return super(InventoryAdmin, self).get_empty_value_display()


admin.site.register(Inventory, InventoryAdmin)
