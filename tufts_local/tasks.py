
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