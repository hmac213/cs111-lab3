# Hash Hash Hash

This lab takes a serial hash table and produces two
thread-safe versions of `add_entry` using `pthread_mutex_t`. `v1` uses one mutex. `v2` uses one mutex per
bucket.

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
Sample output on my test VM (`nproc` = 8, 8 threads × 50,000 entries):
```
Generation: 425,194 usec
Hash table base: 6,941,345 usec
  - 0 missing
Hash table v1: 17,230,584 usec
  - 0 missing
Hash table v2: 1,350,318 usec
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
With 8 threads and 50,000 entries each on my VM (`nproc` = 8):
- Base:  6,941,345 usec
- v1:   17,230,584 usec  (≈ 2.5× slower than base)

Version 1 is *slower* than the base version. This is expected: v1 does
the same work as the base serial implementation (only one thread can be
inside `add_entry` at a time, since the mutex is global), but it also
pays a lot of overhead the serial base version doesn't have:

1. **Thread setup/teardown.** The tester spawns 8 threads with
   `pthread_create` and waits on `pthread_join`. Each `pthread_create`
   call has to allocate a stack, set up TLS, and enter the kernel to
   create a new task; `pthread_join` blocks the main thread waiting on
   each worker. The base version runs entirely on the main thread and
   pays none of this.
2. **Lock acquisition on every insert.** v1 does
   `pthread_mutex_lock` + `pthread_mutex_unlock` on every single call to
   `add_entry` (400,000 times in this run). Even on a single uncontended
   acquire, this is an atomic RMW plus a function call.
3. **Lock contention and cache-line bouncing.** All 8 threads are
   fighting over the *same* mutex. Every successful acquire/release
   bounces the cache line holding the mutex word between cores, and
   threads that don't win the lock either spin briefly or get descheduled
   by the kernel. That's pure overhead — none of it exists in the serial
   base.

Net: v1's wall-clock time is roughly *base time + thread definition
overhead + lock overhead + contention*, so it's strictly worse than
serial. Coarse-grained locking turns the parallel version back into a
slower serial one.

## Second Implementation
In the `hash_table_v2_add_entry` function, I moved the mutex *into*
`struct hash_table_entry` so every bucket has its own
`pthread_mutex_t`. With `HASH_TABLE_CAPACITY = 4096` that means 4096
independent locks. Each lock is initialized in `hash_table_v2_create`
(once we've called `SLIST_INIT` on the bucket) and destroyed in
`hash_table_v2_destroy` after the bucket's list has been freed.

`add_entry` first computes the bucket via `get_hash_table_entry` (this
is a pure read of an array index and doesn't touch any shared mutable
state, so it doesn't need a lock). To minimize time-under-lock, the new
`list_entry` is `calloc`'d *before* the lock is taken — the allocator
has its own internal locks, and pulling that call out of the critical
section means threads contending on the same bucket don't also serialize
on `calloc`. The bucket mutex is then taken only around the linear
search and the linked-list update. Both exit paths unlock before
returning; if the key turned out to already exist, the speculatively
allocated node is `free`'d after unlocking so we don't leak. All
`pthread_mutex_*` return values are checked.

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
With 8 threads and 50,000 entries each on my VM (`nproc` = 8):
- Base:  6,941,345 usec
- v1:   17,230,584 usec  (≈ 2.5× *slower* than base)
- v2:    1,350,318 usec  (≈ 5.14× *faster* than base)

v2 is **≈ 5.1× faster than the base** with 8 threads — a substantial
speedup that scales with thread count, in contrast to v1's regression.
The speedup is sub-linear (you might expect closer to 8× on 8 cores)
for a few reasons:

1. **Thread create/join is still on the critical path** and is roughly
   constant overhead independent of the work done per thread.
2. **Bucket collisions.** With only 4096 buckets and 400,000 inserts
   the average bucket holds ~100 entries, and any two threads inserting
   into the same bucket are serialized on that bucket's mutex.
3. **Residual allocator contention.** Each insert still calls `calloc`
   for a new `list_entry`; pulling it out of the critical section helps,
   but glibc's allocator still has internal locks that can occasionally
   serialize concurrent allocations.
4. **Logical vs. physical cores.** `nproc` reports logical CPUs; if the
   VM's 8 vCPUs are backed by 4 physical cores with SMT, the practical
   parallelism ceiling is closer to 4–5×, not 8×.

But the dominant cost — walking and mutating the bucket's linked list —
now runs concurrently for disjoint buckets, which is exactly what
per-bucket locking buys us. Compared to v1's single global mutex, v2
turns ~4096-way potential parallelism into actual parallel work for
threads that happen to land on different buckets.

## Cleaning up
```shell
make clean
```
