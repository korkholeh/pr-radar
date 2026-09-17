# Generated for phase 5 — AISignal.evidence_hash + idempotency constraint, DetectionRule.name unique.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ai_detection', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='aisignal',
            name='evidence_hash',
            field=models.CharField(default='', max_length=64, verbose_name='evidence hash'),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='detectionrule',
            name='name',
            field=models.CharField(max_length=200, unique=True, verbose_name='name'),
        ),
        migrations.AddConstraint(
            model_name='aisignal',
            constraint=models.UniqueConstraint(
                fields=('pull_request', 'rule', 'evidence_hash'), name='uniq_aisignal_pr_rule_evidence'
            ),
        ),
    ]
