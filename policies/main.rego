# Policy-as-code rules evaluated against `terraform show -json <planfile>`
# during validation (Step 2) and before every deployment (Step 7).
# Each rule maps to an organizational policy from Section 3 of the paper.
# Site-specific values (approved datastores, allowed versions) live in
# policies/data/tdgov.yaml and are passed to conftest with --data.

package main

import rego.v1

resources contains r if {
	some r in input.planned_values.root_module.resources
}

vms contains r if {
	some r in resources
	r.type == "proxmox_virtual_environment_vm"
}

has_tag(vm, tag) if {
	some t in vm.values.tags
	t == tag
}

# TDGOV-001: all VM disks on approved encrypted storage.
deny contains msg if {
	some vm in vms
	some d in vm.values.disk
	not d.datastore_id in data.tdgov.encrypted_datastores
	msg := sprintf("TDGOV-001 %s: disk datastore '%s' is not an approved encrypted datastore", [vm.address, d.datastore_id])
}

# TDGOV-001b: cloud-init drives (which may hold injected secrets) on encrypted storage.
deny contains msg if {
	some vm in vms
	some i in vm.values.initialization
	not i.datastore_id in data.tdgov.encrypted_datastores
	msg := sprintf("TDGOV-001 %s: cloud-init datastore '%s' is not an approved encrypted datastore", [vm.address, i.datastore_id])
}

# TDGOV-002: the database is attached to the private subnet only.
deny contains msg if {
	some vm in vms
	has_tag(vm, "role-db")
	some n in vm.values.network_device
	n.bridge != input.variables.private_bridge.value
	msg := sprintf("TDGOV-002 %s: database NIC on bridge '%s'; only the private bridge '%s' is allowed", [vm.address, n.bridge, input.variables.private_bridge.value])
}

# TDGOV-003: no default database port.
deny contains msg if {
	input.variables.db_port.value == 5432
	msg := "TDGOV-003 db_port: the default PostgreSQL port 5432 is not allowed"
}

# TDGOV-004: only supported PostgreSQL major versions (prevents version debt).
deny contains msg if {
	v := input.variables.postgres_version.value
	not v in data.tdgov.allowed_postgres_versions
	msg := sprintf("TDGOV-004 postgres_version: '%s' is not in the supported versions %v", [v, data.tdgov.allowed_postgres_versions])
}

# TDGOV-005: ownership and role tags on every VM (traceability, unmanaged scan).
deny contains msg if {
	some vm in vms
	not has_tag(vm, "managed-by-terraform")
	msg := sprintf("TDGOV-005 %s: missing tag 'managed-by-terraform'", [vm.address])
}

deny contains msg if {
	some vm in vms
	count([t | some t in vm.values.tags; startswith(t, "role-")]) == 0
	msg := sprintf("TDGOV-005 %s: missing a 'role-*' tag", [vm.address])
}

# TDGOV-006: firewall enabled on every network interface.
deny contains msg if {
	some vm in vms
	some n in vm.values.network_device
	n.firewall == false
	msg := sprintf("TDGOV-006 %s: firewall disabled on bridge '%s'", [vm.address, n.bridge])
}

# TDGOV-007: secret-like variables must not have defaults in the blueprint.
secret_name(name) if regex.match(`(?i)(password|passwd|secret|token)`, name)

deny contains msg if {
	some name, v in input.configuration.root_module.variables
	secret_name(name)
	"default" in object.keys(v)
	msg := sprintf("TDGOV-007 variable '%s': secrets must be injected at deployment time, not defaulted in the blueprint", [name])
}

deny contains msg if {
	some name, v in input.configuration.root_module.variables
	secret_name(name)
	not v.sensitive
	msg := sprintf("TDGOV-007 variable '%s': secret variables must be marked sensitive", [name])
}

# TDGOV-008: no hard-coded secret literals in resource arguments.
deny contains msg if {
	some r in input.configuration.root_module.resources
	[path, value] := walk(r.expressions)
	count(path) > 0
	key := path[count(path) - 1]
	key == "constant_value"
	count(path) > 1
	attr := path[count(path) - 2]
	is_string(attr)
	secret_name(attr)
	msg := sprintf("TDGOV-008 %s: hard-coded value for secret attribute '%s'", [r.address, attr])
}

# TDGOV-009: provider versions pinned (reproducible baseline, DR1).
deny contains msg if {
	some name, p in input.configuration.provider_config
	not p.version_constraint
	msg := sprintf("TDGOV-009 provider '%s': version constraint required", [name])
}
