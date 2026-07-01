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


def get_sf_tag(sf_entry, tag_name):
    if 'tags_explicit' not in sf_entry or not sf_entry['tags_explicit']:
        return None
    tag = [i for i in sf_entry['tags_explicit'].split(',') if i.startswith(f"{tag_name}:")]
    return tag[0].split(':')[1] if tag else None


def get_approvers_from_tags(sf_entry):
    if 'tags_explicit' not in sf_entry or not sf_entry['tags_explicit']:
        return None
    tags = [i for i in sf_entry['tags_explicit'].split(',') if i.startswith("Approver:")]
    return [i.split(':')[1] for i in tags] if tags else []


def update_project_approvers_from_subfolder_tags(subfolder_response):
    for s in subfolder_response:
        try:
            proj = get_project_by_key(s['fn'])
            tier1_exists = Allocation.objects.filter(project=proj, resources__name__contains='Tier 1').distinct().exists()
            if tier1_exists:
                # skip tier2 and tier3 allocations since approvers are only relevant for tier1
                if s['volume'] in ['tier2', 'cold']:
                    continue
            #users = ProjectUser.objects.filter(project=proj, user__username__not_in=get_approvers_from_tags(s))
            #for u in users:
            #    u.role = ProjectUserRoleChoice.objects.get(name='User')
            #    u.save()
            for username in get_approvers_from_tags(s):
                user = create_user(username)
                proj_user, _ = ProjectUser.objects.get_or_create(user=user, project=proj,
                                                 defaults={'status': ProjectUserStatusChoice.objects.get(name='Active'),
                                                           'role': ProjectUserRoleChoice.objects.get(name='Manager')})
                proj_user.role = ProjectUserRoleChoice.objects.get(name='Manager')
                proj_user.save()
        except Exception as e:
            print(f"Error processing {s['fn']}: {e}")
            continue


def find_missing_projects(subfolder_response):
    for i in subfolder_response:
        if not project_exists(volpath_to_project_key(i['vol_path'])):
            print(f"{i['vol_path']}")


def find_missing_allocations(resource_name, subfolder_response):
    for i in subfolder_response:
        try:
            proj = get_project_by_key(volpath_to_project_key(i['vol_path']))
            alloc = Allocation.objects.filter(project=proj, resources__name__contains=resource_name).distinct()
            if not alloc.exists():
                print(f"{proj.title} does not have {resource_name} allocation")
        except Exception as e:
            print(f"Error processing {i['vol_path']}: {e}")


def get_sf(project_key, subfolder_response):
    return [i for i in subfolder_response if volpath_to_project_key(i['vol_path']).lower() == project_key.lower()]


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
    print(f"Created {resource_name} allocation for {proj.title}")


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

def get_sf_volumes_in_coldfront():
    """
    Returns a list of all Starfish volumes that are in Coldfront.
    """
    sf_vol_path = AllocationAttributeType.objects.get(name='sf_vol_path')
    volpaths = AllocationAttribute.objects.filter(allocation_attribute_type=sf_vol_path).values_list('value', flat=True)
    return list(set([i.split(':')[0] for i in list(volpaths)]))


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