import requests
import os
from coldfront.config.settings import ENV


def get_ncq_eligibility():
    """
    return a dictionary mapping each username to their NoCostQuota eligibility status.
    """
    headers = {
        'X-Api-Key': ENV.str("RT_ANALYTICS_API_KEY", default=""),
    }
    url = os.path.join(ENV.str("RT_ANALYTICS_BASE_URL"), 'ncq/eligibility')
    start = 0
    all_eligibility = []
    while True:
        response = requests.get(url, headers=headers, params={'start': start, 'rows': 250})
        if response.status_code == 200:
            data = response.json()
            results = data.get('results', [])
            total = data.get('total_count', 0)
            all_eligibility.extend(results)
            if start + len(results) >= total:
                break
            start += len(results)
        else:
            raise Exception(f"Failed to fetch NCQ eligibility: {response.status_code} - {response.text}")
    return all_eligibility