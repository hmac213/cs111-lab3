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
mutex on entry and releases it on every exit path which can occur. The mutex is
created in `hash_table_v1_create` and destroyed in
`hash_table_v1_destroy`. Every `pthread_mutex_*` call checks its return
value and exits on failure, as per the spec.

A single mutex guarantees correctness because the only data races
in the original code happen inside `add_entry`. That is, two threads can create a race condition
while iterating through a bucket's list by both missing the existing key, both
`calloc` a new node, and both `SLIST_INSERT_HEAD`. Putting one
lock around the entire section serializes its operations, so at most one thread is ever mutating the
table at a time. This eliminates any possible race condition.

### Performance
```shell
./hash-table-tester -t 8 -s 50000
```
With 8 threads and 50,000 entries each on my VM (`nproc` = 8):
- Base:  6,941,345 usec
- v1:   17,230,584 usec  (≈ 2.5× slower than base)

V1 is slower than the base version. This is expected since v1 does
the same work as the base implementation but also has to go through a lot of overhead. This includes:

1. **Thread management.** The tester spawns threads and each `pthread_create`
   call has to allocate a stack, set up TLS, and enter the kernel to
   create a new task; `pthread_join` blocks the main thread waiting on
   each worker. The base version runs only on the main thread and
   avoids all of this.
2. **Lock acquisition on every insert.** v1 does
   `pthread_mutex_lock` and `pthread_mutex_unlock` on every single call to
   `add_entry` (400,000 times in this run). Even on a single uncontended
   acquire, this contains overhead.
3. **Lock contention.** All 8 threads are fighting over the same mutex. Every successful acquire/release bounces the cache line holding the mutex word between cores, and threads that don't win the lock either spin briefly or get descheduled by the kernel.

Net: v1's wall-clock time is approximately *base version time + thread
overhead + lock overhead + contention*, so it's strictly worse than
serial. This method of locking turns the parallel version back into a
slower serial one.

## Second Implementation
In the `hash_table_v2_add_entry` function, I moved the mutex into
`struct hash_table_entry` so every bucket has its own
`pthread_mutex_t`. Each lock is initialized in `hash_table_v2_create` and destroyed in
`hash_table_v2_destroy` after the bucket's list has been freed.

`add_entry` first computes the bucket via `get_hash_table_entry` (this
is a read of an array index so it doesn't need a lock). To minimize locked time, the new
`list_entry` is `calloc` before the lock is taken since the allocator
has its own internal locks, and pulling that call out of the critical
section means threads fighting in the same bucket don't also serialize
on `calloc`. The bucket mutex is then taken only around the
search and the linked-list update. Both exit paths unlock before
returning, for if the key turned out to already exist, the
allocated node is `free` after unlocking to avoid leaking. All
`pthread_mutex_*` return values are checked as in v1.

This is correct because the only shared mutable state touched inside
`add_entry` is the bucket's linked list. Two threads inserting into different
buckets touch disjoint memory and may run parallel to each other
safely. Two threads inserting into the same bucket are serialized by
that bucket's mutex, so the same SLIST invariants hold as in v1. The
hash table struct itself is not mutated after `create`, so there's no shared state above the bucket
level. This is not only correct, but much more performant.

### Performance
```shell
./hash-table-tester -t 8 -s 50000
```
With 8 threads and 50,000 entries each on my VM (`nproc` = 8):
- Base:  6,941,345 usec
- v1:   17,230,584 usec  (≈ 2.5× *slower* than base)
- v2:    1,350,318 usec  (≈ 5.14× *faster* than base)

v2 is **≈ 5.1× faster than the base** with 8 threads, in contrast to v1.
The speedup is not as high as we wanted but this may happen for a couple reasons:

1. Thread create/join is roughly
   constant overhead independent of the work done per thread.
2. Bucket collisions can happen. With only 4096 buckets and 400,000 inserts
   the average bucket holds ~100 entries, and any two threads inserting
   into the same bucket become serialized.
4. Logical vs. physical cores on a virtual machine degrade performance. `nproc` reports logical CPUs, so having 8 cores does not necessarily mean the parent machine does as well.

## Cleaning up
```shell
make clean
```
