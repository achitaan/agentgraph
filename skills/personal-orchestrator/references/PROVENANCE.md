# Owned source and attribution

This self-contained skill is an editable local fork of selected LaneOrchestrator
control-plane code plus original toolkit policy and execution code. It has no
upstream import, plugin, marketplace, installation, or download dependency.

Upstream: [LaneOrchestrator commit 9890a6b96675d1fb129bf44ea560a492ead2c780](https://github.com/KarthikRamesh9149/laneorchestrator/tree/9890a6b96675d1fb129bf44ea560a492ead2c780).
Exact source was recovered from the existing toolkit Git tree at
`b4f998e9b77c2a60e193cb5d7047238d70460864`, without a new download. The original
[MIT license](LICENSE.lane) is unchanged. Original additions use this
[skill's MIT license](../LICENSE).

[source-map.json](source-map.json) records original file hashes and owned
locations. Current files intentionally differ: namespaces and roles changed,
fixed model defaults were removed, prompt exports no longer claim to enforce
sandbox settings, and profile adoption cannot consume upstream roles.
Configuration restoration, durable dispatch, the separate-session adapter and
its integration tests are local additions. The copied usage accountant also
recognizes running calls with unknown final usage.

Nine recovered modules retain ownership checks, no-follow I/O, private locking,
expiring one-use previews, state checks, receipts and bounded executable probes.
The specialist pack, plugin registration and fixed model roster are excluded.
See [module parity](upstream-parity.md) for replacements and remaining exclusions.
Source ownership and test evidence do not establish live permissions, account
compatibility, or lower token costs.
