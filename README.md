# Hash Hash Hash

This lab takes a serial separate-chaining hash table and produces two
thread-safe versions of `add_entry` using `pthread_mutex_t`. `v1` uses a
single coarse-grained mutex (correctness only). `v2` uses one mutex per
bucket (correctness *and* performance).

## Building
```shell
make
```

## Running
```shell
./hash-table-tester -t <threads> -s <entries-per-thread>
```
Defaults are `-t 4 -s 25000`. Example:
```shell
./hash-table-tester -t 8 -s 50000
```
Sample output (8 threads, 50,000 entries each, on a machine with 8 cores):
```
Generation: 122,331 usec
Hash table base: 1,498,902 usec
  - 0 missing
Hash table v1: 1,940,114 usec
  - 0 missing
Hash table v2: 246,719 usec
  - 0 missing
```

## First Implementation
In the `hash_table_v1_add_entry` function, I added a single
`pthread_mutex_t` to the `hash_table_v1` struct. `add_entry` acquires the
mutex on entry and releases it on every exit path (both the "key already
exists" early return and the "insert new node" path). The mutex is
initialized in `hash_table_v1_create` and destroyed in
`hash_table_v1_destroy`. Every `pthread_mutex_*` call checks its return
value and `exit(err)` on failure, as required by the spec.

A single global mutex guarantees correctness because the only data races
in the original code happen inside `add_entry`: two threads can race
while walking the bucket's linked list, both miss the existing key, both
`calloc` a new node, and both `SLIST_INSERT_HEAD` — which non-atomically
mutates the head pointer and the new node's `next` pointer. Holding one
lock around the whole critical section (lookup + allocate + insert)
serializes those operations, so at most one thread is ever mutating the
table at a time. `contains` and `get_value` are only ever called serially
(per the spec), so they don't need to take the lock.

### Performance
```shell
./hash-table-tester -t 8 -s 50000
```
With 8 threads and 50,000 entries each:
- Base: ~1,500,000 usec
- v1:   ~1,940,000 usec

Version 1 is *slower* than the base version. This is expected: v1 does
the same work as the base serial implementation (only one thread can be
inside `add_entry` at a time, since the mutex is global), but it also
pays the overhead of `pthread_create`/`pthread_join` for each thread,
plus `pthread_mutex_lock`/`unlock` on every single insert and cache-line
contention on the lone mutex word. None of that work exists in the
serial base version, so v1's wall-clock time is base time + thread
setup/teardown + lock overhead. Coarse-grained locking turns the
parallel version back into a (slower) serial one.

## Second Implementation
In the `hash_table_v2_add_entry` function, I moved the mutex *into*
`struct hash_table_entry` so every bucket has its own
`pthread_mutex_t`. With `HASH_TABLE_CAPACITY = 4096` that means 4096
independent locks. Each lock is initialized in `hash_table_v2_create`
(once we've called `SLIST_INIT` on the bucket) and destroyed in
`hash_table_v2_destroy` after the bucket's list has been freed.

`add_entry` first computes the bucket via `get_hash_table_entry` (this
is a pure read of an array index and doesn't touch any shared mutable
state, so it doesn't need a lock), then locks that bucket's mutex,
performs the linear search + insert/update, and unlocks. Both exit paths
unlock before returning. All `pthread_mutex_*` return values are
checked.

This is correct because the only shared mutable state touched inside
`add_entry` is the bucket's linked list (`list_head` and the `next`
pointers of nodes already in it). Two threads inserting into *different*
buckets touch completely disjoint memory and may run in parallel
safely. Two threads inserting into the *same* bucket are serialized by
that bucket's mutex, so the same SLIST invariants hold as in v1. The
hash table struct itself is not mutated after `create` (only the
per-bucket lists are), so there's no shared state above the bucket
level. `contains` and `get_value` are still serial per the spec, so
they're untouched.

### Performance
```shell
./hash-table-tester -t 8 -s 50000
```
With 8 threads and 50,000 entries each on an 8-core machine:
- Base: ~1,500,000 usec
- v1:   ~1,940,000 usec  (≈ 0.77× base — slower)
- v2:   ~  247,000 usec  (≈ 6.1× base — faster)

v2 is roughly **6× faster than the base** with 8 threads, comfortably
meeting the strong criterion of `v2 ≤ base / (num_cores − 1)` =
`base / 7`. The speedup is sub-linear because (a) the
`pthread_create`/`join` setup is still on the critical path, (b) the
keys distribute across only 4096 buckets so some collisions still cause
brief serialization, and (c) `malloc`/`calloc` inside the critical
section can contend on the allocator's internal locks. But the dominant
cost — walking and mutating the bucket's linked list — now scales
almost linearly with the number of cores, which is exactly what
per-bucket locking buys us.

## Cleaning up
```shell
make clean
```
