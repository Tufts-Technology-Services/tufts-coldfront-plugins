
from coldfront.core.project.models import Project, ProjectUser
from tufts_local.starfish_utils import get_starfish_usage_data_by_volume
from tufts_local.utils import update_project_approvers_from_subfolder_tags


def update_project_approvers_from_tags():
    for volume in ['projects', 'tier2', 'cold', 'cold2', 'rstore-cifs', 'rstore-nfs']:
        subfolder_response = get_starfish_usage_data_by_volume(volume, 'starfish')
        update_project_approvers_from_subfolder_tags(subfolder_response)


def update_sf_approvers():
    # write starfish tags corresponding to project approvers for all projects in Coldfront
    all_projects = Project.objects.all()
    for proj in all_projects:
        approvers = ProjectUser.objects.filter(project=proj, role__name='Manager')
        if approvers.exists():
            approver_usernames = [a.user.username for a in approvers]
            sf_tag_value = ','.join([f"Approver:{username}" for username in approver_usernames])
            sf_entry = get_starfish_usage_data_by_volume(proj.key, 'starfish')
            if sf_entry:
                sf_entry = sf_entry[0]
                existing_tags = sf_entry.get('tags_explicit', '')
                # remove any existing Approver tags
                existing_tags_list = [tag for tag in existing_tags.split(',') if not tag.startswith('Approver:')]
                # add the new Approver tags
                new_tags_list = existing_tags_list + [f"Approver:{username}" for username in approver_usernames]
                new_tags_value = ','.join(new_tags_list)
                # update the Starfish entry with the new tags
                from starfish_api_client import StarfishAPIClient  # pylint: ignore=import-outside-toplevel
                client_config = get_client_config('starfish')
                sf_client = StarfishAPIClient(host=client_config['host'], token=client_config['api_key'])
                sf_client.update_subfolder_tags(sf_entry['vol_path'], new_tags_value)


def reclaim_oversubscribed_no_cost_quotas():
    """
    Reclaim any NoCostQuotaAllotments that are associated with an allocation that has exceeded its quota.
    """
    from coldfront_billing.models import NoCostQuotaAllotment
    from coldfront.core.allocation.models import Allocation
    from coldfront.core.resource.models import Resource

    requires_payment = Resource.objects.filter(requires_payment=True, resource_type__name='Storage')
    allocations = Allocation.objects.filter(resources__in=requires_payment, status__name='Active').prefetch_related('allocationattribute_set')

    for allocation in allocations:
        try:
            quota = allocation.allocationattribute_set.filter(allocation_attribute_type__name='reported_usage_bytes').first().value
        except AttributeError:
            continue
        ncq_allotment = NoCostQuotaAllotment.objects.filter(allocation=allocation)
        ncq_allot_total = sum(float(allot.amount) for allot in ncq_allotment)
        if int(quota)/10**12 < ncq_allot_total:
            # reclaim the allotments
            if int(quota) == 0:
                # if the quota is 0, then we can reclaim all allotments
                for allot in ncq_allotment:
                    allot.delete()
            else:
                # split the difference between the quota and the total allotments proportionally among the allotments
                # todo: make sure that the total amount of the allotments is equal to the quota after reclaiming
                # todo: we are only dealing with 100ths of a TB here, so we can just round to 2 decimal places
                difference = ncq_allot_total - int(quota)/10**12
                for allot in ncq_allotment:
                    proportion = float(allot.amount) / ncq_allot_total
                    reclaim_amount = proportion * difference
                    allot.amount -= reclaim_amount
                    if allot.amount <= 0:
                        allot.delete()
                    else:
                        allot.save()
