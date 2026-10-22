package main_test

import rego.v1

import data.main

policy_data := {
	"encrypted_datastores": ["zfs-encrypted"],
	"allowed_postgres_versions": ["16", "17"],
}

vm(address, tags, datastore, bridges) := {
	"address": address,
	"type": "proxmox_virtual_environment_vm",
	"values": {
		"tags": tags,
		"disk": [{"datastore_id": datastore}],
		"initialization": [{"datastore_id": datastore}],
		"network_device": [{"bridge": b, "firewall": true} | some b in bridges],
	},
}

compliant := {
	"planned_values": {"root_module": {"resources": [
		vm("proxmox_virtual_environment_vm.db", ["managed-by-terraform", "role-db"], "zfs-encrypted", ["vmbr1"]),
		vm("proxmox_virtual_environment_vm.lb", ["managed-by-terraform", "role-lb"], "zfs-encrypted", ["vmbr0"]),
	]}},
	"variables": {
		"private_bridge": {"value": "vmbr1"},
		"db_port": {"value": 6432},
		"postgres_version": {"value": "16"},
	},
	"configuration": {
		"provider_config": {"proxmox": {"name": "proxmox", "version_constraint": "~> 0.116"}},
		"root_module": {
			"variables": {"db_admin_password": {"sensitive": true}},
			"resources": [],
		},
	},
}

test_compliant_plan_passes if {
	count(main.deny) == 0 with input as compliant with data.tdgov as policy_data
}

test_unencrypted_disk_denied if {
	bad := json.patch(compliant, [{"op": "replace", "path": "/planned_values/root_module/resources/0/values/disk/0/datastore_id", "value": "local-lvm"}])
	some msg in main.deny with input as bad with data.tdgov as policy_data
	startswith(msg, "TDGOV-001")
}

test_db_on_public_bridge_denied if {
	bad := json.patch(compliant, [{"op": "replace", "path": "/planned_values/root_module/resources/0/values/network_device/0/bridge", "value": "vmbr0"}])
	some msg in main.deny with input as bad with data.tdgov as policy_data
	startswith(msg, "TDGOV-002")
}

test_default_port_denied if {
	bad := json.patch(compliant, [{"op": "replace", "path": "/variables/db_port/value", "value": 5432}])
	some msg in main.deny with input as bad with data.tdgov as policy_data
	startswith(msg, "TDGOV-003")
}

test_eol_postgres_denied if {
	bad := json.patch(compliant, [{"op": "replace", "path": "/variables/postgres_version/value", "value": "12"}])
	some msg in main.deny with input as bad with data.tdgov as policy_data
	startswith(msg, "TDGOV-004")
}

test_missing_tag_denied if {
	bad := json.patch(compliant, [{"op": "replace", "path": "/planned_values/root_module/resources/1/values/tags", "value": ["role-lb"]}])
	some msg in main.deny with input as bad with data.tdgov as policy_data
	startswith(msg, "TDGOV-005")
}

test_firewall_disabled_denied if {
	bad := json.patch(compliant, [{"op": "replace", "path": "/planned_values/root_module/resources/1/values/network_device/0/firewall", "value": false}])
	some msg in main.deny with input as bad with data.tdgov as policy_data
	startswith(msg, "TDGOV-006")
}

test_secret_default_denied if {
	bad := json.patch(compliant, [{"op": "add", "path": "/configuration/root_module/variables/db_admin_password/default", "value": "hunter2"}])
	some msg in main.deny with input as bad with data.tdgov as policy_data
	startswith(msg, "TDGOV-007")
}

test_hardcoded_secret_denied if {
	bad := json.patch(compliant, [{"op": "add", "path": "/configuration/root_module/resources/-", "value": {
		"address": "dns_a_record_set.x",
		"expressions": {"password": {"constant_value": "hunter2"}},
	}}])
	some msg in main.deny with input as bad with data.tdgov as policy_data
	startswith(msg, "TDGOV-008")
}

test_unpinned_provider_denied if {
	bad := json.patch(compliant, [{"op": "remove", "path": "/configuration/provider_config/proxmox/version_constraint"}])
	some msg in main.deny with input as bad with data.tdgov as policy_data
	startswith(msg, "TDGOV-009")
}
