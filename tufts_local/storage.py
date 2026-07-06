import datetime
import logging

from coldfront.core.allocation.models import AllocationAttribute
from coldfront.core.resource.models import Resource
from coldfront_utils import update_allocation_attribute_value, bytes_to_units

from storage.constants import QUOTA_IN_BYTES_ATTRIBUTE_NAME, QUOTA_ATTRIBUTE_NAME, QUOTA_REPORT_DATE_ATTRIBUTE_NAME, STORAGE_PLUGIN_STORAGE_UNITS
from storage.utils import get_client_config
from storage.starfish import get_path_usage_data, validate_starfish_path, get_starfish_usage_data_by_volume

logger = logging.getLogger(__name__)


def get_quotas_batch(resource_id=None, client_config_id=None):
    """
    for tier3, we want to set quota to usage for all allocations of a given resource, based on the reported usage from Starfish.
    """
    resource = Resource.objects.get(id=resource_id)
    client_config = get_client_config(client_config_id)
    # to take full advantage of caching, we need to group by volume
    path_attr = client_config['native_path_attribute_name']

    # log a warning for Allocations of this resource that are missing a native path attribute 
    resource_allocations = resource.allocation_set.distinct()
    for alloc in resource_allocations:
        if not alloc.allocationattribute_set.filter(allocation_attribute_type__name=path_attr).exists():
            logger.warning(f"Allocation {alloc.pk} of resource {resource.name} is missing required native path attribute '{path_attr}' and will be skipped in Starfish usage retrieval task.")
        else:
            # check value of native path attribute and log a warning if it does not match expected format of "volume_name:path/to/subfolder"
            native_path_value = alloc.allocationattribute_set.filter(allocation_attribute_type__name=path_attr).first().value
            try:
                validate_starfish_path(native_path_value)
            except ValueError as e:
                logger.warning(f"Allocation {alloc.pk} of resource {resource.name} has native path attribute value '{native_path_value}' that is invalid: {e}. This allocation will be skipped in Starfish usage retrieval task.")

    # now filter to only include allocations with valid native path attributes
    sf_attrs = AllocationAttribute.objects.filter(allocation__in=resource_allocations,
        allocation_attribute_type__name=path_attr)
    # get a set of all unique volumes
    volumes = set(i.split(':')[0] for i in sf_attrs.values_list("value", flat=True).distinct())

    for vol in volumes:
        vol_attributes = sf_attrs.filter(value__startswith=f"{vol}:")
        volume_data = get_starfish_usage_data_by_volume(vol, client_config_id)
        for vol_path in vol_attributes:
            try:
                validate_starfish_path(vol_path.value)
            except ValueError as e:
                logger.warning(f"Skipping allocation {vol_path.allocation.pk} with invalid Starfish path '{vol_path.value}': {e}")
                continue
            usage, report_date = get_path_usage_data(volume_data, vol_path.value)
            if usage and report_date:
                logger.info(f"Updating usage for allocation {vol_path.allocation.pk} with usage {usage} bytes and report date {report_date}")
                report_date = datetime.datetime.now() # TrueNAS API does not provide a timestamp for when the quota information was last updated, so we will use the current time as the report date
                update_allocation_attribute_value(vol_path.allocation, 
                                                  QUOTA_IN_BYTES_ATTRIBUTE_NAME, 
                                                  usage)
                update_allocation_attribute_value(vol_path.allocation, QUOTA_ATTRIBUTE_NAME, str(round(bytes_to_units(usage, STORAGE_PLUGIN_STORAGE_UNITS), 2))) 
                update_allocation_attribute_value(vol_path.allocation, QUOTA_REPORT_DATE_ATTRIBUTE_NAME, report_date.isoformat())
            else:
                logger.warning(f"No matching subfolder found for allocation attribute with vol_path value {vol_path.value}")
    return True