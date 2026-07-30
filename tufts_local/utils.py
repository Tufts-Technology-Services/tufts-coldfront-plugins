import datetime
import logging
from coldfront.core.project.models import (Project, 
                                            ProjectAttribute, 
                                            ProjectAttributeType, 
                                            ProjectUser, 
                                            ProjectUserRoleChoice,
                                            ProjectUserStatusChoice,
                                            ProjectStatusChoice)
from coldfront.core.allocation.models import Allocation, AllocationAttribute, AllocationAttributeType, AllocationStatusChoice
from coldfront.core.resource.models import Resource
from django.contrib.auth.models import User

from coldfront_utils.util.ad_search import ADSearch


logger = logging.getLogger(__name__)


def project_exists(project_key):
    return ProjectAttribute.objects.filter(value__iexact=project_key, proj_attr_type__name='Project Key').exists()


def is_duplicate(project_key):
    matches = ProjectAttribute.objects.filter(value__iexact=project_key, proj_attr_type__name='Project Key')
    if matches.count() > 1:
        print(f"Duplicate project key found: {project_key}")
        for match in matches:
            print(f" - Project ID: {match.project.id}, Title: {match.project.title}")
        return True
    return False


def get_project_by_key(project_key):
    match = ProjectAttribute.objects.filter(value__iexact=project_key, proj_attr_type__name='Project Key')
    if match.count() == 1:
        return match.first().project
    elif match and len(match) > 1:
        print(f"Multiple projects found with key: {project_key}")
        for m in match:
            print(f" - Project ID: {m.project.id}, Title: {m.project.title}")
        raise Exception(f"Multiple projects found with key: {project_key}")
    print(f"No project found with key: {project_key}")
    return None


def volpath_to_project_key(volpath):
    return volpath.split(':')[1]


def create_tufts_project(project_key, owner, group=None):
    if project_exists(project_key):
        return get_project_by_key(project_key)
    pi, created = User.objects.get_or_create(username=owner)
    if created:
        print(f'Created new user {owner}')
    proj, created = Project.objects.get_or_create(title=project_key, pi=pi, status=ProjectStatusChoice.objects.get(name='Active'))
    ProjectUser.objects.get_or_create(user=pi, role=ProjectUserRoleChoice.objects.get(name='Manager'), project=proj, status=ProjectUserStatusChoice.objects.get(name='Active'))
    if group:
        ProjectAttribute.objects.get_or_create(proj_attr_type=ProjectAttributeType.objects.get(name='Group'), value=group, project=proj)
    ProjectAttribute.objects.get_or_create(proj_attr_type=ProjectAttributeType.objects.get(name='Project Key'), value=project_key, project=proj)
    return proj


def update_project_owner(project_key, new_owner):
    proj = get_project_by_key(project_key)
    if not proj:
        raise Exception(f"No project found with key: {project_key}")
    new_pi = User.objects.filter(username=new_owner)
    if new_pi.exists():
        new_pi = new_pi.first()
    else:
        new_pi = create_user(new_owner)
        
    proj.pi = new_pi
    proj.save()
    # Update ProjectUser for the new PI
    project_user, _ = ProjectUser.objects.get_or_create(user=new_pi, project=proj, 
                                                        defaults={'status': ProjectUserStatusChoice.objects.get(name='Active'), 
                                                                  'role': ProjectUserRoleChoice.objects.get(name='Manager')})
    return proj


def find_missing_projects(subfolder_response):
    for i in subfolder_response:
        if not project_exists(volpath_to_project_key(i['vol_path'])):
            logger.warning(f"{i['vol_path']}")


def find_missing_allocations(resource_name, subfolder_response):
    for i in subfolder_response:
        try:
            proj = get_project_by_key(volpath_to_project_key(i['vol_path']))
            alloc = Allocation.objects.filter(project=proj, resources__name__contains=resource_name).distinct()
            if not alloc.exists():
                logger.warning(f"{proj.title} does not have {resource_name} allocation")
        except Exception as e:
            logger.error(f"Error processing {i['vol_path']}: {e}")


def create_allocation(project_key, resource_name, sf_entry):
    proj = get_project_by_key(project_key)
    alloc = Allocation.objects.filter(project=proj, resources__name=resource_name)
    if not alloc.exists():
        if 'rec_aggrs' not in sf_entry or 'min' not in sf_entry['rec_aggrs'] or 'ctime' not in sf_entry['rec_aggrs']['min']:
            create_time = datetime.datetime.now()            
        else:
            create_time = datetime.datetime.fromtimestamp(sf_entry['rec_aggrs']['min']['ctime'])
        alloc = Allocation.objects.create(project=proj, 
                                          start_date=create_time, 
                                          end_date=datetime.datetime.now() + datetime.timedelta(days=5000), 
                                          status=AllocationStatusChoice.objects.get(name="Active"))
        alloc.resources.add(Resource.objects.get(name=resource_name))
    AllocationAttribute.objects.get_or_create(allocation=alloc, allocation_attribute_type=AllocationAttributeType.objects.get(name='sf_vol_path'), value=sf_entry['vol_path'])
    logger.info(f"Created {resource_name} allocation for {proj.title}")


class UserNotFoundError(Exception):
    pass


def create_user(username):
    username = username.lower().strip()
    if not username:
        logger.warning("No username provided")
        raise ValueError("No username provided")
    user_search_obj = ADSearch(username, "username_only")
    result = user_search_obj.search_a_user(user_search_string=username, search_by="username_only")
    if len(result) == 0:
        logger.warning(f"No user found for {username}")
        raise UserNotFoundError(f"No user found for {username}")
    if len(result) > 1:
        logger.warning(f"Multiple users found for {username}")
        raise UserNotFoundError(f"Multiple users found for {username}")
    matches = result[0]
    user_obj, created = User.objects.get_or_create(username=matches.get("username"))
    if created or not user_obj.first_name or not user_obj.last_name or not user_obj.email:
        user_obj.first_name = matches.get("first_name")
        user_obj.last_name = matches.get("last_name")
        user_obj.email = matches.get("email")
        user_obj.save()
    return user_obj


def entry_exists(entry_name):
    entry_name = entry_name.lower().strip()
    if not entry_name:
        logger.error("No entry name provided")
        raise ValueError("No entry name provided")
    search_obj = ADSearch(entry_name, "all_object_names")
    result = search_obj.search_a_user(user_search_string=entry_name, search_by="all_object_names")
    return len(result) > 0


def user_autocomplete(search_string):
    search_obj = ADSearch(search_string, "autocomplete")
    result = search_obj.search_a_user(user_search_string=search_string, search_by="autocomplete")
    return result


def delete_allocation_by_volpath(vol_path):
    """
    Deletes an allocation based on the Starfish vol_path.
    """
    try:
        alloc_attr = AllocationAttribute.objects.get(allocation_attribute_type__name='sf_vol_path', value__iexact=vol_path)
        alloc = alloc_attr.allocation
        project = alloc.project
        alloc.delete()
        logger.info(f"Deleted allocation for vol_path: {vol_path}")
        delete_project_if_no_allocations(project)
    except AllocationAttribute.DoesNotExist:
        logger.warning(f"No allocation found for vol_path: {vol_path}")


def delete_project_if_no_allocations(proj):
    """
    Deletes a project if it has no allocations.
    """
    if not proj:
        logger.warning(f"No project found")
        return
    if not Allocation.objects.filter(project=proj).exists():
        proj.delete()
        logger.info(f"Deleted project with title: {proj.title}")
    else:
        logger.info(f"Project with title: {proj.title} has allocations and was not deleted")


def allocation_date_info_updated(allocation, attribute_type_name, days=1):
    """
    Returns True if the allocation's info has been updated in the last `days` days.
    """
    if not allocation:
        raise ValueError("No allocation provided")
    attribs = allocation.allocationattribute_set.values('value', 'allocation_attribute_type__name')
    match = next((item for item in attribs if item["allocation_attribute_type__name"] == attribute_type_name), None)
    if not match or not match['value']:
        logger.warning(f"Allocation {allocation.id} has no {attribute_type_name} attribute or value")
        return (False, None)
    return ((datetime.datetime.now() - datetime.datetime.fromisoformat(match['value'])).total_seconds() <= days * 86400), datetime.datetime.fromisoformat(match['value'])


def not_updated_report():
    """
    Returns a list of allocations where the info has not been updated in the last `days` days.
    """
    storage = Resource.objects.filter(resource_type__name="Storage")
    allocations = Allocation.objects.filter(resources__in=storage, status__name="Active").distinct().prefetch_related('allocationattribute_set', 'project__pi', 'project')
    not_updated_allocations = []
    for alloc in allocations:
        qrd_updated = allocation_date_info_updated(alloc, attribute_type_name="quota_report_date", days=1)
        usage_updated = allocation_date_info_updated(alloc, attribute_type_name="usage_report_date", days=1)
        if not qrd_updated[0] or not usage_updated[0]:
            not_updated_allocations.append({"allocation": alloc, "project_title": alloc.project.title, "pi": alloc.project.pi, "qrd_updated": qrd_updated, "usage_updated": usage_updated})
    return {"not_updated_allocations": not_updated_allocations}


def setup_custom_logger(name, log_file, level=logging.INFO):
    """Function to dynamically configure separate loggers."""
    # Define a clean layout structure
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Create the file handler bound to your target filename
    handler = logging.FileHandler(log_file)        
    handler.setFormatter(formatter)

    # Instantiate the independent logger object
    custom_logger = logging.getLogger(name)
    custom_logger.setLevel(level)
    custom_logger.addHandler(handler)
    
    return custom_logger
