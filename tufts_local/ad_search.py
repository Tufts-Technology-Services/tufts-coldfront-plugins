from coldfront.plugins.ldap_user_search.utils import LDAPUserSearch
import logging
import json
import ldap.filter
from ldap3 import get_config_parameter, set_config_parameter

logger = logging.getLogger(__name__)


class TuftsADSearch(LDAPUserSearch):
    
    def search_a_user(self, user_search_string=None, search_by="all_fields"):
        size_limit = 50
        ldap_attrs = list(self.ATTRIBUTE_MAP.values())
        attrs = get_config_parameter("ATTRIBUTES_EXCLUDED_FROM_CHECK")
        attrs.extend(ldap_attrs)
        set_config_parameter("ATTRIBUTES_EXCLUDED_FROM_CHECK", attrs)
        if user_search_string and search_by == "all_fields":
            filter = ldap.filter.filter_format(
                f"(&(|({ldap_attrs[0]}=*%s*)({ldap_attrs[1]}=*%s*)({ldap_attrs[2]}=*%s*)({ldap_attrs[3]}=*%s*))(objectclass=person))",
                [user_search_string] * 4,
            )
        elif user_search_string and search_by == "username_only":
            attr = self.USERNAME_ONLY_ATTR
            filter = ldap.filter.filter_format(f"(&({self.ATTRIBUTE_MAP[attr]}=%s)(objectclass=person))", [user_search_string])
            size_limit = 1
        elif user_search_string and search_by == "group_only":
            attr = self.USERNAME_ONLY_ATTR
            filter = ldap.filter.filter_format(f"(&({self.ATTRIBUTE_MAP[attr]}=%s)(objectclass=group))", [user_search_string])
            size_limit = 1
        elif user_search_string and search_by == "all_object_names":
            attr = self.USERNAME_ONLY_ATTR
            filter = ldap.filter.filter_format(f"({self.ATTRIBUTE_MAP[attr]}=%s)", [user_search_string])
            size_limit = 1
        elif user_search_string and search_by in self.ATTRIBUTE_MAP.keys():
            filter = ldap.filter.filter_format(f"(&({self.ATTRIBUTE_MAP[search_by]}=%s)(objectclass=person))", [user_search_string])
            size_limit = 1
        else:
            filter = "(objectclass=person)"

        searchParameters = {
            "search_base": self.LDAP_USER_SEARCH_BASE,
            "search_filter": filter,
            "attributes": ldap_attrs,
            "size_limit": size_limit,
        }
        logger.debug(f"search params: {searchParameters}")
        self.conn.search(**searchParameters)
        users = []
        for idx, entry in enumerate(self.conn.entries, 1):
            entry_dict = json.loads(entry.entry_to_json()).get("attributes")
            logger.debug(f"Entry dict: {entry_dict}")
            user_dict = self.MAPPING_CALLBACK(self.ATTRIBUTE_MAP, entry_dict)
            user_dict["source"] = self.search_source
            users.append(user_dict)
        logger.info("LDAP user search for %s found %s results", user_search_string, len(users))
        return users