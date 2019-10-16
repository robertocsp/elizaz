import base64
import hashlib
import logging
from datetime import datetime, timedelta, timezone

from dateutil.relativedelta import relativedelta
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.utils.deprecation import MiddlewareMixin
from mws import mws, Feeds as FeedsMWS, utils, MWSError
from mws.mws import calc_md5

from amazonseller.settings import MWS_ACCESS_KEY, MWS_SECRET_KEY
from utils.helper import mws_normalize_condition

logger = logging.getLogger(__name__)


def to_md5(string):
    md5_hash = hashlib.md5()
    md5_hash.update(string)
    return base64.b64encode(md5_hash.digest()).strip(b'\n')


class Feeds(FeedsMWS):
    def submit_feed(self, feed, feed_type, marketplaceids=None,
                    content_type="text/xml", purge='false'):
        """
        Uploads a feed ( xml or .tsv ) to the seller's inventory.
        Can be used for creating/updating products on Amazon.
        """
        md = to_md5(feed)
        data = dict(Action='SubmitFeed',
                    FeedType=feed_type,
                    PurgeAndReplace=purge,
                    ContentMD5Value=md)
        data.update(utils.enumerate_param('MarketplaceIdList.Id.', marketplaceids))
        return self.make_request(data, method="POST", body=feed,
                                 extra_headers={'Content-Type': content_type})


def build_product_feed_body(seller_id, items):
    amz_envelope = ''.join(['<?xml version="1.0" ?>'] +
                           ['<AmazonEnvelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '] +
                           ['xsi:noNamespaceSchemaLocation="amznenvelope.xsd">'] +
                           ['<Header>'] +
                           ['<DocumentVersion>1.01</DocumentVersion>'] +
                           ['<MerchantIdentifier>%(seller_id)s</MerchantIdentifier>' % {'seller_id': seller_id}] +
                           ['</Header>'] +
                           ['<MessageType>Product</MessageType>'] +
                           ['<PurgeAndReplace>false</PurgeAndReplace>'] +
                           ['<Message>'
                            '<MessageID>%(index)s</MessageID>'
                            '<Product>'
                            '<SKU>%(sku)s</SKU>'
                            '<StandardProductID>'
                            '<Type>UPC</Type>'
                            '<Value>%(upc)s</Value>'
                            '</StandardProductID>'
                            '<Condition>'
                            '<ConditionType>%(condition)s</ConditionType>'
                            '</Condition>'
                            '</Product>'
                            '</Message>' % {'index': (index + 1),
                                            'sku': item.sku,
                                            'upc': item.upc,
                                            'condition': mws_normalize_condition(item.condition)}
                            for index, item in enumerate(items)] +
                           ['</AmazonEnvelope>'])
    logger.debug('============PRODUCT============')
    logger.debug(amz_envelope.encode('utf-8'))
    return amz_envelope.encode('utf-8')


def build_product_delete_feed_body(seller_id, items):
    amz_envelope = ''.join(['<?xml version="1.0" ?>'] +
                           ['<AmazonEnvelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '] +
                           ['xsi:noNamespaceSchemaLocation="amznenvelope.xsd">'] +
                           ['<Header>'] +
                           ['<DocumentVersion>1.01</DocumentVersion>'] +
                           ['<MerchantIdentifier>%(seller_id)s</MerchantIdentifier>' % {'seller_id': seller_id}] +
                           ['</Header>'] +
                           ['<MessageType>Product</MessageType>'] +
                           ['<Message>'
                            '<MessageID>%(index)s</MessageID>'
                            '<OperationType>Delete</OperationType>'
                            '<Product>'
                            '<SKU>%(sku)s</SKU>'
                            '</Product>'
                            '</Message>' % {'index': (index + 1),
                                            'sku': item.sku} for index, item in enumerate(items)] +
                           ['</AmazonEnvelope>'])
    logger.debug('============PRODUCT DELETE============')
    logger.debug(amz_envelope.encode('utf-8'))
    return amz_envelope.encode('utf-8')


def build_price_feed_body(seller_id, items):
    amz_envelope = ''.join(['<?xml version="1.0" ?>'] +
                           ['<AmazonEnvelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '] +
                           ['xsi:noNamespaceSchemaLocation="amznenvelope.xsd">'] +
                           ['<Header>'] +
                           ['<DocumentVersion>1.01</DocumentVersion>'] +
                           ['<MerchantIdentifier>%(seller_id)s</MerchantIdentifier>' % {'seller_id': seller_id}] +
                           ['</Header>'] +
                           ['<MessageType>Price</MessageType>'] +
                           ['<Message>'
                            '<MessageID>%(index)s</MessageID>'
                            '<Price>'
                            '<SKU>%(sku)s</SKU>'
                            '<StandardPrice currency="USD">%(price)s</StandardPrice>'
                            '</Price>'
                            '</Message>' % {'index': (index + 1),
                                            'sku': item.sku,
                                            'price': item.standard_price} for index, item in enumerate(items)] +
                           ['</AmazonEnvelope>'])
    logger.debug('============PRICE============')
    logger.debug(amz_envelope.encode('utf-8'))
    return amz_envelope.encode('utf-8')


def get_item_handling_time(item):
    return int(float(item.handling_time))


def build_inventory_feed_body(seller_id, items):
    amz_envelope = ''.join(['<?xml version="1.0" ?>'] +
                           ['<AmazonEnvelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '] +
                           ['xsi:noNamespaceSchemaLocation="amznenvelope.xsd">'] +
                           ['<Header>'] +
                           ['<DocumentVersion>1.01</DocumentVersion>'] +
                           ['<MerchantIdentifier>%(seller_id)s</MerchantIdentifier>' % {'seller_id': seller_id}] +
                           ['</Header>'] +
                           ['<MessageType>Inventory</MessageType>'] +
                           ['<Message>'
                            '<MessageID>%(index)s</MessageID>'
                            '<OperationType>Update</OperationType>'
                            '<Inventory>'
                            '<SKU>%(sku)s</SKU>'
                            '<Quantity>%(qty)s</Quantity>'
                            '<FulfillmentLatency>%(handling)s</FulfillmentLatency>'
                            '</Inventory>'
                            '</Message>' % {'index': (index + 1),
                                            'sku': item.sku,
                                            'qty': int(float(item.quantity)),
                                            'handling': get_item_handling_time(item)}
                            for index, item in enumerate(items)] +
                           ['</AmazonEnvelope>'])
    logger.debug('============INVENTORY============')
    logger.debug(amz_envelope.encode('utf-8'))
    return amz_envelope.encode('utf-8')


class ThrottlingException(Exception):
    def __init__(self, *args):
        Exception.__init__(self, *args)


class DataCorruptionException(Exception):
    def __init__(self, *args):
        Exception.__init__(self, *args)


def update_store(store, items, operation='update'):
    seller_id = store.seller_id
    auth_token = store.auth_token
    store_last_execution = store.last_execution
    store_name = store.name
    feeds_api = mws.Feeds(access_key=MWS_ACCESS_KEY,
                          secret_key=MWS_SECRET_KEY,
                          account_id=seller_id,
                          auth_token=auth_token)
    # NO THROTTLING -> MINUTES=0
    if store_last_execution is None or datetime.now(tz=timezone.utc) >= (store_last_execution + timedelta(minutes=0)):
        if operation == 'update':
            product_return = feeds_api.submit_feed(build_product_feed_body(seller_id, items), '_POST_PRODUCT_DATA_')
            price_return = feeds_api.submit_feed(build_price_feed_body(seller_id, items), '_POST_PRODUCT_PRICING_DATA_')
            inventory_return = feeds_api.submit_feed(build_inventory_feed_body(seller_id, items),
                                                     '_POST_INVENTORY_AVAILABILITY_DATA_')
            # logger.info(product_return.response.headers)
            # logger.info(price_return.response.headers)
            # logger.info(inventory_return.response.headers)
            return datetime.now(tz=timezone.utc), product_return.parsed, price_return.parsed, inventory_return.parsed
            # SAVE DATETIME NOW FOR THE 20 MINUTES CHECK
        elif operation == 'delete':
            product_return = feeds_api.submit_feed(build_product_delete_feed_body(seller_id, items),
                                                   '_POST_PRODUCT_DATA_')
            return datetime.now(tz=timezone.utc), product_return.parsed, None, None
    else:
        time_left = (store_last_execution + timedelta(minutes=20)) - datetime.now(tz=timezone.utc)
        minutes = time_left.seconds // 60
        raise ThrottlingException('Throttling: %(store)s - %(minutes)s minute(s) remaining' % {'store': store_name,
                                                                                               'minutes': minutes})


def store_inventory(seller_id, auth_token):
    inventory_api3 = mws.Inventory(access_key=MWS_ACCESS_KEY,  # INFO NOSSA (24U/Idea Shop)
                                   secret_key=MWS_SECRET_KEY,  # INFO NOSSA (24U/Idea Shop)
                                   account_id=seller_id,  # INFO LOJA (Seller ID)
                                   auth_token=auth_token)  # INFO LOJA
    date = datetime.now()
    date = date + relativedelta(days=-1)
    inventory_list = inventory_api3.list_inventory_supply(datetime_=date.isoformat())
    # print(str(inventory_list.parsed))
    while 'NextToken' in inventory_list.parsed:
        inventory_list = inventory_api3.list_inventory_supply(next_token=inventory_list.parsed['NextToken']['value'])
        # print(str(inventory_list.parsed))


def get_items(seller_id, auth_token, items):
    products_api = mws.Products(access_key=MWS_ACCESS_KEY,  # INFO NOSSA (24U/Idea Shop)
                                secret_key=MWS_SECRET_KEY,  # INFO NOSSA (24U/Idea Shop)
                                account_id=seller_id,  # INFO LOJA (Seller ID)
                                auth_token=auth_token)  # INFO LOJA
    products = products_api.get_matching_product_for_id('ATVPDKIKX0DER', 'UPC', items)
    return products


def get_feed_submission_list(seller_id, auth_token, feed_ids):
    feeds_api = mws.Feeds(access_key=MWS_ACCESS_KEY,
                          secret_key=MWS_SECRET_KEY,
                          account_id=seller_id,
                          auth_token=auth_token)
    feed_submission_return = feeds_api.get_feed_submission_list(feedids=feed_ids,
                                                                feedtypes=['_POST_PRODUCT_DATA_',
                                                                           '_POST_PRODUCT_PRICING_DATA_',
                                                                           '_POST_INVENTORY_AVAILABILITY_DATA_'],
                                                                processingstatuses=['_DONE_', '_CANCELLED_',
                                                                                    '_AWAITING_ASYNCHRONOUS_REPLY_',
                                                                                    '_IN_PROGRESS_', '_IN_SAFETY_NET_',
                                                                                    '_SUBMITTED_', '_UNCONFIRMED_'])
    return feed_submission_return.parsed['FeedSubmissionInfo']


def get_feed_submission_result(seller_id, auth_token, feed_id):
    feeds_api = mws.Feeds(access_key=MWS_ACCESS_KEY,
                          secret_key=MWS_SECRET_KEY,
                          account_id=seller_id,
                          auth_token=auth_token)
    feed_submission_result_return = feeds_api.get_feed_submission_result(feed_id)
    content_md5 = calc_md5(feed_submission_result_return.response.content).decode('utf-8')
    if feed_submission_result_return.response.headers['Content-MD5'] != content_md5:
        logger.error('DATA CORRUPTION')
        logger.error(feed_submission_result_return.original)
        logger.error('header md5 :: %(header_md5)s != content md5 :: %(content_md5)s' %
                     {'header_md5': feed_submission_result_return.response.headers['Content-MD5'],
                      'content_md5': content_md5})
        raise DataCorruptionException()
    processing_report = feed_submission_result_return.parsed['ProcessingReport']
    result_status = ['_DONE_']
    log_result = False
    if processing_report['StatusCode']['value'] != 'Complete':
        log_result = True
        result_status.append(processing_report['StatusCode']['value'].upper())
        result_status.append('_')
    processing_summary = processing_report['ProcessingSummary']
    if processing_summary:
        messages_with_error = int(processing_summary['MessagesWithError']['value'])
        messages_with_warning = int(processing_summary['MessagesWithWarning']['value'])
        if messages_with_error > 0:
            log_result = True
            result_status.append('_WITH_ERROR_')
        if messages_with_error > 0 and messages_with_warning > 0:
            result_status.append('_AND_')
        if messages_with_warning > 0:
            log_result = True
            result_status.append('_WITH_WARNING_')
    if log_result:
        logger.error(feed_submission_result_return.original)
    return ''.join(result_status)


class RedirectToRefererResponse(HttpResponseRedirect):
    def __init__(self, request, *args, **kwargs):
        redirect_to = request.META.get('HTTP_REFERER', '/')
        super(RedirectToRefererResponse, self).__init__(
            redirect_to, *args, **kwargs)


# Mixin for compatibility with Django <1.10
class HandleBusinessExceptionMiddleware(MiddlewareMixin):
    def process_exception(self, request, exception):
        if isinstance(exception, MWSError):
            message = 'Amazon was unable to process your action, please check the logs for more details.'
            messages.error(request, message)
            logger.error(exception)
            return RedirectToRefererResponse(request)
