from coldfront.plugins.ldap_user_search.utils import LDAPUserSearch
import logging
import json
import ldap.filter
from ldap3 import get_config_parameter, set_config_parameter

logger = logging.getLogger(__name__)


class TuftsADSearch(LDAPUserSearch):
    
    def search_a_user(self, user_search_string=None, search_by="all_fields", size_limit=None):
        ldap_attrs = list(self.ATTRIBUTE_MAP.values())
        attrs = get_config_parameter("ATTRIBUTES_EXCLUDED_FROM_CHECK")
        attrs.extend(ldap_attrs)
        set_config_parameter("ATTRIBUTES_EXCLUDED_FROM_CHECK", attrs)
        if user_search_string and search_by == "all_fields":
            filter = ldap.filter.filter_format(
                f"(&(|({ldap_attrs[0]}=*%s*)({ldap_attrs[1]}=*%s*)({ldap_attrs[2]}=*%s*)({ldap_attrs[3]}=*%s*))(objectclass=person))",
                [user_search_string] * 4,
            )
            size_limit = size_limit or 50
        elif user_search_string and search_by == "username_only":
            attr = self.USERNAME_ONLY_ATTR
            filter = ldap.filter.filter_format(f"(&({self.ATTRIBUTE_MAP[attr]}=%s)(objectclass=person))", [user_search_string])
            size_limit = 1
        elif user_search_string and search_by == "autocomplete":
            filter = ldap.filter.filter_format(
                f"(&(|({ldap_attrs[0]}=*%s*)({ldap_attrs[1]}=*%s*)({ldap_attrs[2]}=*%s*)({ldap_attrs[3]}=*%s*))(objectclass=person))",
                [user_search_string] * 4,
            )
            size_limit = size_limit or 15
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
            size_limit = size_limit or 5
        else:
            filter = "(objectclass=person)"
            size_limit = size_limit or 50

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
    
    def __get_ad_object(self, object_name, filt, ldap_attrs):
        attrs = get_config_parameter("ATTRIBUTES_EXCLUDED_FROM_CHECK")
        attrs.extend(ldap_attrs)
        set_config_parameter("ATTRIBUTES_EXCLUDED_FROM_CHECK", attrs)
        searchParameters = {
            "search_base": self.LDAP_USER_SEARCH_BASE,
            "search_filter": filt,
            "attributes": ldap_attrs,
            "size_limit": 1,
        }
        logger.debug(f"Object search params: {searchParameters}")
        self.conn.search(**searchParameters)
        if self.conn.entries:
            entry_dict = json.loads(self.conn.entries[0].entry_to_json()).get("attributes")
            logger.debug(f"Object entry dict: {entry_dict}")
            return entry_dict
        else:
            logger.info("No AD object found for %s", object_name)
            return None
    
    def get_ad_user(self, username):
        """Search for a user in AD by their username."""
        ldap_attrs = list(self.ATTRIBUTE_MAP.values()) + ['objectSid', 'uidNumber', 'gidNumber']
        filt = ldap.filter.filter_format(f"(&({ldap_attrs[0]}=%s)(objectclass=person))", [username])
        return self.__get_ad_object(username, filt, ldap_attrs)

    def get_ad_group(self, group_name):
        """Search for a group in AD by its name."""
        ldap_attrs = ['sAMAccountName', 'objectSid', 'gidNumber']
        filt = ldap.filter.filter_format(f"(&({ldap_attrs[0]}=%s)(objectclass=group))", [group_name])
        return self.__get_ad_object(group_name, filt, ldap_attrs)
