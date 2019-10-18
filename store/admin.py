import json
import logging

from django import forms
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.contrib.admin.views.main import ChangeList
from django.contrib.auth import get_permission_codename
from django.core.exceptions import PermissionDenied
from django.db import router
from django.db.models import Q
from django.forms import models
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse, NoReverseMatch
from django.contrib.admin.actions import delete_selected as delete_selected_
from django.contrib.admin.utils import (
    quote,
    model_ngettext, NestedObjects)
from django.utils.decorators import method_decorator
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.views.decorators.csrf import csrf_protect

from store.models import Store, StoreForm, StoreFile, inventory_form_factory, Inventory, FeedSubmissionInfo
from utils.aws import update_store, ThrottlingException, get_feed_submission_list, get_feed_submission_result, \
    DataCorruptionException
from utils.thread_local import get_current_user


logger = logging.getLogger(__name__)
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


def update_aws(modeladmin, request, queryset, operation):
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
                        call_mws(objects, previous_obj, throttlings, operation)
                        objects.clear()
                objects.add(obj)
            previous_obj = objects.pop()
            objects.add(previous_obj)
            call_mws(objects, previous_obj, throttlings, operation)
            # modeladmin.log_deletion(request, obj, obj_display)
            if len(throttlings):
                for throttling in throttlings:
                    modeladmin.message_user(request, str(throttling), messages.WARNING)
            else:
                modeladmin.message_user(request, 'Successfully fed %(count)d %(items)s.' % {
                    "count": n, "items": model_ngettext(modeladmin.opts, n)
                }, messages.SUCCESS)
    # Return None to display the change list page again.
    return None


def call_mws(objects, previous_obj, throttling, operation):
    try:
        store = Store.objects.get(seller_id=previous_obj.store.seller_id)
        store.last_execution, product_return, price_return, inventory_return = update_store(store,
                                                                                            objects,
                                                                                            operation)
        store.save()
        feed_infos = []
        if product_return:
            feed_infos.append(save_return(product_return['FeedSubmissionInfo'], store))
        if price_return:
            feed_infos.append(save_return(price_return['FeedSubmissionInfo'], store))
        if inventory_return:
            feed_infos.append(save_return(inventory_return['FeedSubmissionInfo'], store))
        for item in objects:
            item.sync_status = 1
            for feed_info in feed_infos:
                item.feed_submission_info.add(feed_info)
        Inventory.objects.bulk_update(objects, ['sync_status'])
    except ThrottlingException as e:
        throttling.add(e)


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
    return feed_info


class FeedObjects(NestedObjects):
    def collect(self, objs, source=None, source_attr=None, **kwargs):
        for obj in objs:
            self.add_edge(None, obj)
            self.model_objs[obj._meta.model].add(obj)

    def _nested(self, obj, seen, format_callback):
        if obj in seen:
            return []
        seen.add(obj)
        if format_callback:
            ret = [format_callback(obj)]
        else:
            ret = [obj]
        return ret

    def nested(self, format_callback=None):
        """
        Return the graph as a nested list.
        """
        seen = set()
        roots = []
        for root in self.edges.get(None, ()):
            roots.extend(self._nested(root, seen, format_callback))
        return roots


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
    collector = FeedObjects(using=using)
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


def _check_feed_status(modeladmin, request, select_store_template):
    if request.user.is_superuser:
        store = request.POST.get('check_store_action')
    else:
        store = request.user.store.id
    if not store and request.POST.get('post'):
        modeladmin.message_user(request, 'Store must be selected in order to perform action on it.', messages.WARNING)
    elif store and request.POST.get('post'):
        logger.debug('=== STORE ===')
        logger.debug(store)
        store = Store.objects.get(pk=store)
        feed_submission_info_list = FeedSubmissionInfo.objects.filter(
            Q(feed_processing_status='_SUBMITTED_') | Q(feed_processing_status='_IN_PROGRESS_'),
            store=store)
        if feed_submission_info_list:
            feeds = [feed_submission_info.feed_submission_id for feed_submission_info in feed_submission_info_list]
            feed_submission_list = get_feed_submission_list(
                store.seller_id,
                store.auth_token,
                feeds)
            for feed_submission in feed_submission_list:
                feed_submission_info = FeedSubmissionInfo.objects.get(feed_submission_id=
                                                                      feed_submission['FeedSubmissionId']['value'])
                feed_submission_info.feed_processing_status = feed_submission['FeedProcessingStatus']['value']
                if 'StartedProcessingDate' in feed_submission:
                    feed_submission_info.started_processing_date = feed_submission['StartedProcessingDate']['value']
                if 'CompletedProcessingDate' in feed_submission:
                    feed_submission_info.completed_processing_date = \
                        feed_submission['CompletedProcessingDate']['value']
                if feed_submission_info.feed_processing_status == '_DONE_':
                    try:
                        feed_submission_info.feed_processing_status = get_feed_submission_result(
                            store.seller_id,
                            store.auth_token,
                            feed_submission['FeedSubmissionId']['value']
                        )
                    except DataCorruptionException:
                        feed_submission_info.feed_processing_status = '_DATA_CORRUPTION_'
                feed_submission_info.save()
            if len(feeds):
                modeladmin.message_user(request, 'The following feeds have been checked: %(items)s' % {
                    "items": ', '.join(feeds)
                }, messages.INFO)
        return HttpResponseRedirect(reverse('admin:%s_%s_changelist' % ('store', 'feedsubmissioninfo'),
                                            current_app='admin'))
    context = {
        **modeladmin.admin_site.each_context(request),
        'title': 'Store selection',
        'stores': Store.objects.all() if request.user.is_superuser else Store.objects.none(),
        'media': modeladmin.media,
    }
    request.current_app = modeladmin.admin_site.name
    return TemplateResponse(request, select_store_template, context)


class InventoryAdmin(admin.ModelAdmin):
    add_form = InventoryCreationForm
    change_form = InventoryChangeForm
    list_display = ('_feed_status', 'upc', '_asin', '_item_name', 'sku', 'sku_vendor', 'cost_price', 'drop_fee',
                    'shipment_price', 'standard_price', 'quantity', 'condition', 'handling_time', 'wholesale_name',
                    'csv_update_number', 'csv_datetime', 'csv_filename',)
    list_display_links = ('upc',)
    readonly_fields = ('_feed_status', 'csv_filename', 'csv_datetime', 'csv_update_number')
    fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('_feed_status', 'store', 'upc', 'sku', 'sku_vendor', 'cost_price', 'drop_fee', 'shipment_price',
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
    list_filter = ('csv_update_number',)
    action_form = ActionForm
    actions = ['custom_delete_selected', 'sync_inventory', 'check_sync_status']
    list_per_page = 1000

    def feed_status_image(self, i):
        switcher = {
            0: lambda: mark_safe(
                '<img src="/static/admin/img/icon-no.svg" alt="Not fed" title="Not fed">'
            ),
            1: lambda: mark_safe(
                '<img src="/static/admin/img/icon-yes.svg" alt="Fed" title="Fed">'
            ),
            2: lambda: mark_safe(
                '<img src="/static/admin/img/icon-alert.svg" alt="Awaiting check feed status" '
                'title="Awaiting check feed status">'
            ),
        }
        func = switcher.get(i, lambda: 'Invalid')
        return func()

    def _feed_status(self, obj):
        return self.feed_status_image(obj.sync_status)

    def _asin(self, obj):
        return '-' if obj.asin is None else obj.asin

    def _item_name(self, obj):
        return '-' if obj.item_name is None else obj.item_name

    def custom_delete_selected(self, request, queryset):
        if not self.has_delete_permission(request):
            raise PermissionDenied
        if request.POST.get('post'):
            update_aws(self, request, queryset, 'delete')
            delete_selected_(self, request, queryset)
            return None
        return delete_selected_(self, request, queryset)

    custom_delete_selected.short_description = 'Delete selected objects'

    def check_sync_status(self, request, queryset):
        return _check_feed_status(self, request, 'admin/store/inventory/select_store.html')

    check_sync_status.short_description = "Check Feed Status"
    check_sync_status.allowed_permissions = ('sync',)

    def sync_inventory(self, request, queryset):
        if request.POST.get('post'):
            update_aws(self, request, queryset, 'update')
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

    sync_inventory.short_description = "Feed Inventory -> Marketplace"
    sync_inventory.allowed_permissions = ('sync',)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

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


class FeedSubmissionInfoAdmin(admin.ModelAdmin):
    list_display = ('feed_submission_id',
                    'feed_type',
                    'feed_processing_status',
                    '_feed_items',
                    'submitted_date',
                    'started_processing_date',
                    'completed_processing_date',
                    'store',)
    fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('feed_submission_id',
                       'feed_type',
                       'feed_processing_status',
                       '_feed_items',
                       'submitted_date',
                       'started_processing_date',
                       'completed_processing_date',
                       'store',),
        }),
    )
    list_filter = ('feed_processing_status',)

    actions = ['check_sync_status', 'view_feed_items']

    def get_deleted_objects(self, objs, request):
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
            using = router.db_for_write(FeedSubmissionInfo)
        collector = FeedObjects(using=using)
        collector.collect(objs)
        perms_needed = set()

        def format_callback(obj):
            no_edit_link = 'Feed Submission Info: %s' % (obj,)

            if not self.has_delete_permission(request, obj):
                perms_needed.add('FeedSubmissionInfo')
            try:
                admin_url = reverse('%s:store_feedsubmissioninfo_change'
                                    % (self.admin_site.name,),
                                    None, (quote(obj.pk),))
            except NoReverseMatch:
                # Change url doesn't exist -- don't display link to edit
                return no_edit_link

            # Display a link to the admin page.
            return format_html('Feed Submission Info: <a href="{}">{}</a>',
                               admin_url,
                               obj)

        to_delete = collector.nested(format_callback)

        protected = [format_callback(obj) for obj in collector.protected]
        model_count = {'FeedSubmissionInfo': len(objs) for model, objs in collector.model_objs.items()}

        return to_delete, model_count, perms_needed, protected

    def check_sync_status(self, request, queryset):
        return _check_feed_status(self, request, 'admin/store/feedsubmissioninfo/select_store.html')

    check_sync_status.short_description = "Check Feed Status"

    def view_feed_items(self, request, queryset):
        context = {
            **self.admin_site.each_context(request),
            'title': 'Inventory Items',
            'feedSubmissionInfo': queryset[0],
            'items': queryset[0].inventory_set.all(),
            'media': self.media,
        }
        request.current_app = self.admin_site.name
        return TemplateResponse(request, 'admin/store/feedsubmissioninfo/inventory_items.html', context)

    def _feed_items(self, obj):
        return mark_safe(
            '<a href="javascript: void(0)" onclick="django.jQuery(\'%s\')[0].value = \'%s\'; '
            'django.jQuery(\'%s\').attr({type: \'%s\', name: \'%s\', value: \'%s\'}).appendTo(\'%s\'); '
            'django.jQuery(\'%s\')[0].submit();" '
            'class="viewlink"></a>&nbsp;&nbsp;(%s)' %
            ('#feedsubmissioninfo-form-action', 'view_feed_items', '<input>', 'hidden', '_selected_action', obj.pk,
             '#changelist-form', '#changelist-form', obj.inventory_set.count())
        )
    _feed_items.short_description = 'Feed Items'

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser or request.user.store:
            return super().has_delete_permission(request, obj)
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser or request.user.store:
            return super().has_view_permission(request, obj)
        return False

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            if request.user.store:
                qs = qs.filter(store=request.user.store.id)
            else:
                qs = qs.filter(store=None)
        return qs


admin.site.register(FeedSubmissionInfo, FeedSubmissionInfoAdmin)
