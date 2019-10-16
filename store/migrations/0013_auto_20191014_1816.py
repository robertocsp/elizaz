# Generated by Django 2.2.5 on 2019-10-14 21:16

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0012_auto_20191010_1803'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='inventory',
            options={'permissions': (('sync_inventory', 'Can feed permission'),), 'verbose_name': 'Inventory', 'verbose_name_plural': 'Inventory'},
        ),
        migrations.AddField(
            model_name='inventory',
            name='feed_submission_info',
            field=models.ManyToManyField(blank=True, null=True, to='store.FeedSubmissionInfo'),
        ),
        migrations.AlterField(
            model_name='feedsubmissioninfo',
            name='completed_processing_date',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Complete Processing Date'),
        ),
    ]