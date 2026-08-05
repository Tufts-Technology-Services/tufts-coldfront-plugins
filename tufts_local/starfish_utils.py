import logging
from time import sleep
from coldfront.core.project.models import (ProjectUser, ProjectUserRoleChoice, ProjectUserStatusChoice)
from coldfront.core.allocation.models import (Allocation, AllocationAttribute, 
                                              AllocationAttributeType)
from coldfront_utils import ttl_cache
from storage.utils import get_client_config
from tufts_local.utils import create_user, get_project_by_key, volpath_to_project_key

logger = logging.getLogger(__name__)

@ttl_cache(timeout=60*60)
def get_starfish_usage_data_by_volume(volume: str, client_key: str) -> list:
    """
    Helper function to query Starfish API for usage data for all subfolders of a given volume. 
    Caches results to avoid redundant API calls.
    """
    sf = get_starfish_client(client_key)
    subfolder_response = sf.request_subfolder_query(volume)
    # we only need certain fields from the response, so we will extract those and store them in a list of dictionaries
    retained_fields = ['vol_path', 'logical_size', 'sync', 'username', 'groupname', 'tags_explicit']
    subfolder_response = [{field: i[field] for field in retained_fields} for i in subfolder_response]
    return subfolder_response


def get_starfish_data_by_vol_path(vol_path: str, client_key: str) -> dict:
    """
    Helper function to query Starfish API for usage data for a specific subfolder by its volume path. 
    Caches results to avoid redundant API calls.
    """
    try:
        volume, _ = vol_path.split(":", 1)
    except ValueError as e:
        raise ValueError(f"Invalid vol_path format: '{vol_path}'. Expected format 'volume:path'.") from e
    subfolder_response = get_starfish_usage_data_by_volume(volume, client_key)
    sf_entry = next((item for item in subfolder_response if item['vol_path'].lower() == vol_path.lower()), None)
    if not sf_entry:
        logger.warning(f"Subfolder with vol_path '{vol_path}' not found in Starfish.")
        return None
    return sf_entry


def get_starfish_volumes(client_key: str) -> list:
    """
    Helper function to query Starfish API for a list of volumes. 
    Caches results to avoid redundant API calls.
    """
    #sf = get_starfish_client(client_key)
    return ['cold', 'projects', 'rstore-cifs', 'rstore-nfs', 'tier2', 'other']  


def get_starfish_client(client_key: str):
    """
    Helper function to get a Starfish API client instance.
    """
    from starfish_api_client import StarfishAPIClient # pylint: ignore=import-outside-toplevel
    client_config = get_client_config(client_key)
    return StarfishAPIClient(host=client_config['host'], token=client_config['api_key'])


def add_directory_to_index(vol_path, client_key, wait=5, retries=12):
    """
    Add a top level directory to the index by initiating a scan of depth 0. 
    This is useful when a new project directory is created and needs to be tagged.
    """
    client = get_starfish_client(client_key)
    volume, path = vol_path.split(":", 1)
    r = client.scan_new(volume, path)
    scan_id = r['id']
    for _ in range(retries):
        sleep(wait)
        status = client.get_scan(scan_id)
        if not status['state']['is_running']:
            if status['state']['is_successful']:
                sleep(wait)
                return status
            else:
                raise RuntimeError(f"Scan {scan_id} failed with error: {status['reason']}")

    raise TimeoutError(f"Scan {scan_id} did not complete in time.")


def flatten_tags(tags: dict) -> list:
    """
    Flatten a dictionary of tags into a list of strings in the format 'key:value'.
    """
    flattened = []
    for key, values in tags.items():
        for value in values:
            flattened.append(f"{key}:{value}")
    return flattened


def parse_tags(tags: list) -> dict:
    """
    Parse a list of tags into a dictionary where keys are tag types and values are a set of tag values
    """
    parsed_tags = {}
    for tag in tags:
        if ':' in tag:
            key, value = tag.split(':', 1)
            parsed_tags.setdefault(key.strip(), set()).add(value.strip())
        else:
            logger.warning(f"Tag '{tag}' does not contain a namespace (colon) and will be added to 'other' category.")
            parsed_tags.setdefault('other', set()).add(tag.strip())
    return parsed_tags


def sync_approver_tags(vol_path, approvers: list, client_key=None):
    """
    Synchronize approver tags for a directory indexed by Starfish.
    This function will add or remove approver tags based on the current approvers in Coldfront.
    It will not add tags that are already present, and it will remove tags 
    that are not in the provided list. 
    It will ignore tag types that are not represented in the provided list.
    If the directory is not indexed, it will raise a ValueError
    """
    sf_data = get_starfish_data_by_vol_path(vol_path, client_key)
    if not sf_data:
        raise ValueError(f"Directory with vol_path '{vol_path}' is not indexed in Starfish.")
    existing_tags = parse_tags(sf_data.get('tags_explicit', '').split(','))
    existing_approvers = existing_tags.get('Approver', set())
    new_approvers = set(approvers)
    tags_to_add = {'Approver': new_approvers - existing_approvers}
    tags_to_remove = {'Approver': existing_approvers - new_approvers}
    client = get_starfish_client(client_key)
    # retrieving starfish data by vol_path is case-insensitive, but adding/removing tags is case-sensitive, so we need to use the vol_path from the starfish data
    vol_path = sf_data.get('vol_path')
    tags_to_add = flatten_tags(tags_to_add)
    if tags_to_add:
        client.add_tag(vol_path, tags_to_add)
    tags_to_remove = flatten_tags(tags_to_remove)
    if tags_to_remove:
        client.detach_tag(vol_path, tags_to_remove)
    return {'vol_path': vol_path, 'added': tags_to_add, 'removed': tags_to_remove}


def set_project_approvers_from_starfish(vol_path_data):
    if not vol_path_data:
        logger.warning(f"No data found for vol_path {vol_path_data['vol_path']} in Starfish.")
        return
    try:
        project_key = volpath_to_project_key(vol_path_data['vol_path'])
        proj = get_project_by_key(project_key)
        tier1_exists = Allocation.objects.filter(project=proj, resources__name__contains='Tier 1', status__name='Active').distinct().exists()
        if tier1_exists:
            # skip tier2 and tier3 allocations since approvers are only relevant for tier1
            if vol_path_data['vol_path'].split(':')[0] in ['tier2', 'cold']:
                return
        existing_tags = parse_tags(vol_path_data.get('tags_explicit', '').split(','))
        existing_approvers = existing_tags.get('Approver', set())
        for username in existing_approvers:
            user = create_user(username)
            proj_user, _ = ProjectUser.objects.get_or_create(user=user, project=proj,
                                             defaults={'status': ProjectUserStatusChoice.objects.get(name='Active'),
                                                       'role': ProjectUserRoleChoice.objects.get(name='Manager')})
            proj_user.role = ProjectUserRoleChoice.objects.get(name='Manager')
            proj_user.save()
    except Exception as e:
        logger.error(f"Error processing {vol_path_data['vol_path']}: {e}")
        

def sync_tags(vol_path, tags: list, client_key):
    """
    Synchronize tags for a directory indexed by Starfish.
    This function will add or remove tags based on the current tags in Starfish.
    It will not add tags that are already present, and it will remove tags 
    that are not in the provided list. 
    It will ignore tag types that are not represented in the provided list.
    If the directory is not indexed, it will raise a ValueError
    """
    sf_data = get_starfish_data_by_vol_path(vol_path, client_key)  # raises ValueError if not found
    existing_tags = parse_tags(sf_data.get('tags_explicit', '').split(','))
    project_tags = parse_tags(tags)
    tags_to_add = {}
    tags_to_remove = {}
    for key, value in project_tags.items():
        if key in existing_tags:
            # match values
            tags_to_add[key] = value - existing_tags[key]
            tags_to_remove[key] = existing_tags[key] - value
        else:
            # add all values for this key
            tags_to_add[key] = value
    client = get_starfish_client(client_key)
    tags_to_add = flatten_tags(tags_to_add)
    if tags_to_add:
        client.add_tag(vol_path, tags_to_add)
    tags_to_remove = flatten_tags(tags_to_remove)
    if tags_to_remove:
        client.detach_tag(vol_path, tags_to_remove)


def set_owner_tag(client_key, vol_path, owner: str):
    # valid tags: Owner, Group, LabGroup, Approver, Reporting
    sync_tags(vol_path, [f"Owner:{owner.lower()}"], client_key)


def set_approver_tags(client_key, vol_path, approvers: list):
    # valid tags: Owner, Group, LabGroup, Approver, Reporting
    sync_tags(vol_path, [f"Approver:{approver.lower()}" for approver in approvers], client_key)


def match_owner_tags():
    all_vol_paths = AllocationAttribute.objects.filter(allocation_attribute_type__name="sf_vol_path")
    tag_compare = []
    for vp in all_vol_paths:
        vol_path = vp.value
        sf = get_starfish_data_by_vol_path(vol_path, 'starfish')  # raises ValueError if not found
        if not sf:
            print(f"Vol path {vol_path} not found in Starfish.")
            continue
        tags = parse_tags(sf.get('tags_explicit', '').split(','))
        if not tags:
            print(f"No tags found for vol_path {vol_path}")
            sf_owner = set()
        else:
            sf_owner = tags.get('Owner', set())
        if len(sf_owner) > 0:
            sf_owner = list(sf_owner)[0]
        else:
            sf_owner = ''
        tag_compare.append({'vol_path': vol_path, 'sf_owner': sf_owner, 'coldfront_owner': vp.allocation.project.pi.username})
    return tag_compare


def get_sf_volumes_in_coldfront():
    """
    Returns a list of all Starfish volumes that are in Coldfront.
    """
    sf_vol_path = AllocationAttributeType.objects.get(name='sf_vol_path')
    volpaths = AllocationAttribute.objects.filter(allocation_attribute_type=sf_vol_path).values_list('value', flat=True)
    return list(set([i.split(':')[0] for i in list(volpaths)]))


def add_to_starfish_index(vol_path, client_key, timeout=300, wait=5):
    """
    Add a top level directory to the index by initiating a scan of depth 0. 
    This is useful when a new project directory is created and needs to be tagged.
    """
    client = get_starfish_client(client_key)
    volume, path = vol_path.split(":", 1)
    r = client.scan_new(volume, path)
    scan_id = r['id']
    for _ in range(timeout // wait):  # retry for up to timeout seconds
        sleep(wait)
        status = client.get_scan(scan_id)
        if not status['state']['is_running']:
            if status['state']['is_successful']:
                sleep(5)  # wait a bit for the scan to complete
                return status
            else:
                raise RuntimeError(f"Scan {scan_id} failed with error: {status['reason']}")
    raise TimeoutError(f"Scan {scan_id} did not complete in time.")