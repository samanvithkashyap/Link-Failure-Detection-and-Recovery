# SDN Link Failure Detection and Recovery

**Project #14 — SDN Mininet Based Simulation**

An SDN application built on the Ryu OpenFlow controller and Mininet
that detects link failures in a looped topology and automatically
restores connectivity by letting Spanning Tree Protocol reconverge
onto a redundant path.

---

## Problem Statement

> **Detect link failures and update routing dynamically.**

In a network with only one path between hosts, a single broken cable
takes everything down. This project demonstrates the SDN solution:
a centralized controller watches the switches, notices the instant a
link goes down, flushes the now-invalid forwarding state, and lets
the network re-learn a new path on the fly — no manual intervention.

## Design

### Topology

```
        h1
        |
        s1
       /  \
      /    \
    s2 ---- s3
    |        |
    h2       h3
```

Three OpenFlow 1.3 switches in a triangle, one host per switch. The
triangle forms a **physical loop on purpose** — without redundancy
there is no backup path to recover onto when a link fails.

### Why STP

A loop in a plain learning switch creates a broadcast storm. The
controller runs **Spanning Tree Protocol** through `ryu.lib.stplib`.
At startup, STP blocks one of the three inter-switch links, turning
the triangle into a tree. The blocked link sits idle as a hot
standby. Bridge priorities are set so s1 always becomes the root
bridge.

### Detection and Recovery

The controller handles three STP events from `stplib`:

| Event | What we do |
| --- | --- |
| `EventPacketIn` | Learn source MAC; forward or flood |
| `EventPortStateChange` | Log port transitions (detection evidence) |
| `EventTopologyChange` | Flush learned MAC mappings and flow rules |

When a link goes down, STP transitions affected ports through
`FORWARD → BLOCK → LISTEN → LEARN → FORWARD`. Once STP has settled,
`EventTopologyChange` fires, the controller deletes stale flow rules,
and the next packet triggers re-learning on the new path. Recovery is
end-to-end automatic.

### Design Choices

- **Ryu** — actively maintained, native OpenFlow 1.3 support, built-in STP library.
- **STP over custom routing** — correct, robust, well-understood.
- **Layer-2 learning** — the project is about link recovery, not routing; L2 is the simplest correct layer.

## Files

```
recovery_controller.py   # Ryu controller (SDN logic)
topology.py              # Mininet triangle topology
```

## Setup

Tested on Ubuntu 24.04.

```bash
# System packages
sudo apt install mininet openvswitch-switch

# Python 3.9 (Ubuntu 24 ships 3.12, which Ryu does not support)
sudo apt install python3.9 python3.9-venv python3.9-dev
# If apt can't find it:
#   sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update

# Virtualenv for Ryu
python3.9 -m venv sdn_env
source sdn_env/bin/activate
pip install 'pip<24' 'setuptools<68' 'wheel'
pip install 'eventlet==0.30.2' 'ryu==4.34'
```

## Running

Open two terminals.

**Terminal 1 — controller:**
```bash
source sdn_env/bin/activate
ryu-manager --observe-links recovery_controller.py
```

Wait ~40 seconds for STP to converge (watch for `state=FORWARD` messages).

**Terminal 2 — Mininet:**
```bash
sudo python3 topology.py
```

At the `mininet>` prompt:

```
mininet> pingall
*** Results: 0% dropped (6/6 received)

mininet> link s2 s3 down
mininet> pingall
*** Results: 50% dropped (3/6 received)       # during STP reconvergence

mininet> pingall
*** Results: 0% dropped (6/6 received)        # recovery complete

mininet> link s2 s3 up
mininet> pingall
*** Results: 0% dropped (6/6 received)
```

If pingall still shows losses after a link event, wait ~20 more
seconds and re-run — STP takes up to 30 seconds to finish walking
ports through `LISTEN → LEARN → FORWARD`.

If Mininet leaves switches behind between runs: `sudo mn -c`.

## Test Scenarios

| # | Scenario | Commands | Expected Result |
| - | --- | --- | --- |
| 1 | Normal | `pingall` on startup | 0% dropped |
| 2 | Failure | `link s2 s3 down`, immediate `pingall` | Partial loss during reconvergence |
| 3 | Recovery | `pingall` ~30 s later | 0% dropped — traffic on backup path |
| 4 | Restore | `link s2 s3 up`, `pingall` | 0% dropped — original topology |

## Known Limitations

- Initial STP convergence takes ~30–40 seconds. This is the STP
  forward-delay timer (15 s) times two transitions (LISTEN → LEARN
  → FORWARD). Recovery after a link failure takes the same time.
  RSTP would bring this under a second but is out of scope.
- Ryu 4.34 requires Python 3.9. It fails on Python 3.10+ and
  deadlocks on 3.12.
- The controller assumes three switches with DPIDs 1, 2, 3.

## References

1. Ryu Controller Documentation — https://ryu.readthedocs.io/
2. Ryu `stplib` — https://github.com/faucetsdn/ryu/blob/master/ryu/lib/stplib.py
3. Ryu sample `simple_switch_stp_13.py` — starting point for this controller.
4. Mininet Documentation — http://mininet.org/
5. OpenFlow 1.3 Specification, Open Networking Foundation.
6. IEEE 802.1D Spanning Tree Protocol.
