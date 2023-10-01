from django.db import migrations

LEASE_STATUSES = ("Draft", "Rejected", "Approved")


def reset_out_of_range_statuses(apps, schema_editor):
    # Document.save() used to copy "Pending" onto the lease while a review was
    # queued. A lease awaiting its first verdict is a draft.
    Lease = apps.get_model("lease", "Lease")
    Lease.objects.exclude(status__in=LEASE_STATUSES).update(status="Draft")


class Migration(migrations.Migration):
    dependencies = [("lease", "0010_alter_document_options_alter_lease_options_and_more")]

    operations = [migrations.RunPython(reset_out_of_range_statuses, migrations.RunPython.noop)]
