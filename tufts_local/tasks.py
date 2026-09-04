import datetime
import logging

from coldfront_billing.models import NoCostQuota, NoCostQuotaAllotment
from django.contrib.auth.models import Group, User
from django_q.tasks import Schedule, schedule

from coldfront.core.allocation.models import Allocation, AllocationAttribute
from coldfront.core.project.models import Project, ProjectUser
from coldfront.core.resource.models import Resource

from tufts_local.analytics_utils import get_ncq_eligibility
from tufts_local.starfish_utils import (
    add_to_starfish_index,
    get_starfish_usage_data_by_volume,
    get_starfish_volumes,
    set_owner_tag,
    set_project_approvers_from_starfish,
    sync_approver_tags,
)
from tufts_local.utils import create_user, setup_custom_logger

logger = logging.getLogger(__name__)


def update_project_approvers_from_tags():
    for volume in get_starfish_volumes('starfish'):
        subfolder_response = get_starfish_usage_data_by_volume(volume, 'starfish')
        for vol_path_data in subfolder_response:
            set_project_approvers_from_starfish(vol_path_data)


def update_sf_approver_tags(project_id):
    # write starfish tags corresponding to project approvers for all projects in Coldfront
    updates = []
    proj = Project.objects.get(id=project_id)
    approvers = set(
        ProjectUser.objects.filter(project=proj, role__name='Manager').values_list('user__username', flat=True)
    )
    approvers = approvers - {proj.pi.username}

    alloc_attr = AllocationAttribute.objects.filter(
        allocation__project=proj, allocation_attribute_type__name='sf_vol_path'
    )
    for attr in alloc_attr:
        vol_path = attr.value
        update = sync_approver_tags(vol_path, list(approvers), 'starfish')
        updates.append(update)
    return updates


def update_sf_owner_tags_from_allocations():
    # write starfish tag corresponding to project owner for all allocations in Coldfront
    storage = Resource.objects.filter(resource_type__name='Storage')
    allocations = Allocation.objects.filter(status__name='Active', resources__in=storage).prefetch_related(
        'allocationattribute_set', 'project__pi'
    )
    for alloc in allocations:
        _set_sf_owner_tag(alloc)


def set_sf_owner_tag(allocation_id):
    # write starfish tag corresponding to project owner for all allocations in Coldfront
    storage = Resource.objects.filter(resource_type__name='Storage')
    alloc_set = Allocation.objects.filter(id=allocation_id, resources__in=storage).prefetch_related(
        'allocationattribute_set', 'project__pi'
    )
    if not alloc_set.exists():
        logger.debug(f"Allocation {allocation_id} does not have a storage resource. Skipping Starfish 'Owner' tagging.")
        return
    alloc = alloc_set.first()
    _set_sf_owner_tag(alloc)


def _set_sf_owner_tag(allocation):
    owner = allocation.project.pi.username
    alloc_attr = allocation.allocationattribute_set.filter(allocation_attribute_type__name='sf_vol_path').first()
    if alloc_attr:
        vol_path = alloc_attr.value
        logger.debug(
            f"Setting Starfish 'Owner' tag for allocation {allocation.id} with vol_path {vol_path} to {owner}."
        )
        set_owner_tag('starfish', vol_path, owner)
    else:
        logger.error(
            f"Allocation {allocation.id} does not have an 'sf_vol_path' attribute. Cannot set Starfish 'Owner' tag."
        )


def refresh_ncq_eligibility():
    ncq_logger = setup_custom_logger('ncq', 'ncq.log')
    eligibility_data = get_ncq_eligibility()
    tier1_group_name = 'faculty_pi_eligible_tier1'
    tier2_group_name = 'faculty_pi_eligible_tier2'
    group_tier1 = Group.objects.get(name=tier1_group_name)
    group_tier2 = Group.objects.get(name=tier2_group_name)
    tier1_users = User.objects.filter(groups=group_tier1).values_list('username', flat=True)
    tier2_users = User.objects.filter(groups=group_tier2).values_list('username', flat=True)
    for entry in eligibility_data:
        is_eligible = entry.get('no_cost_quota_eligible', 'No')
        username = entry.get('username', '').lower()
        in_tier_1 = username in tier1_users
        in_tier_2 = username in tier2_users
        if is_eligible.lower().strip() == 'yes':
            if not in_tier_1:
                ncq_logger.info(f'Adding user {username} to group {tier1_group_name}')
                user = create_user(username)
                user.groups.add(group_tier1)
            if not in_tier_2:
                ncq_logger.info(f'Adding user {username} to group {tier2_group_name}')
                user = create_user(username)
                user.groups.add(group_tier2)
        else:
            if in_tier_1:
                ncq_logger.info(f'Removing user {username} from group {tier1_group_name}')
                user = create_user(username)
                user.groups.remove(group_tier1)
                # delete the allotments associated with this user first so that we can log the details of what is being deleted
                allotments = NoCostQuotaAllotment.objects.filter(no_cost_quota__user=user)
                if allotments.exists():
                    ncq_logger.info(
                        f'User {username} is no longer eligible for NoCostQuota. Deleting {allotments.count()} associated NoCostQuotaAllotment(s).'
                    )
                    for allotment in allotments:
                        ncq_logger.info(
                            f'Deleting NoCostQuotaAllotment of size {allotment.amount_tb} TB associated with allocation {allotment.allocation.id}.'
                        )
                        allotment.delete()
                NoCostQuota.objects.filter(user=user).delete()
            if in_tier_2:
                ncq_logger.info(f'Removing user {username} from group {tier2_group_name}')
                user = create_user(username)
                user.groups.remove(group_tier2)
                # delete the allotments associated with this user first so that we can log the details of what is being deleted
                allotments = NoCostQuotaAllotment.objects.filter(no_cost_quota__user=user)
                if allotments.exists():
                    ncq_logger.info(
                        f'User {username} is no longer eligible for NoCostQuota. Deleting {allotments.count()} associated NoCostQuotaAllotment(s).'
                    )
                    for allotment in allotments:
                        ncq_logger.info(
                            f'Deleting NoCostQuotaAllotment of size {allotment.amount_tb} TB associated with allocation {allotment.allocation.id}.'
                        )
                        allotment.delete()
                NoCostQuota.objects.filter(user=user).delete()


def index_new_allocation(allocation_id, scan_id=None, retries=5, wait=5):
    """
    Index a new allocation in Starfish
    """
    try:
        allocation = Allocation.objects.get(id=allocation_id)
        vol_path_attr = allocation.allocationattribute_set.filter(allocation_attribute_type__name='sf_vol_path')
        if not vol_path_attr.exists():
            raise ValueError(
                f"Allocation {allocation_id} does not have an 'sf_vol_path' attribute. Cannot index in Starfish."
            )

        vol_path = vol_path_attr.first().value
        scan_id, status = add_to_starfish_index(vol_path, scan_id, 'starfish')
        if status is True:
            # do new allocation starfish actions
            set_sf_owner_tag(allocation_id)
            update_sf_approver_tags(allocation.project.id)

        else:
            if retries <= 0:
                raise TimeoutError(f"allocation {vol_path} not yet indexed. can't add tags")
            else:
                schedule(
                    'tufts_local.tasks.index_new_allocation',
                    allocation_id,
                    scan_id,
                    retries - 1,
                    wait,
                    schedule_type=Schedule.ONCE,
                    next_run=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=wait),
                )

            return status
    except Allocation.DoesNotExist as e:
        logger.error(f'Allocation with ID {allocation_id} does not exist. Cannot index in Starfish.')
        raise e
    except Exception as e:
        logger.error(f'Error indexing allocation {allocation_id} in Starfish: {str(e)}')
        raise e
