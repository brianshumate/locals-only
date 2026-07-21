# Code-block runner fixture

One passing block:

```python
total = sum(range(10))
assert total == 45
print(total)
```

One failing block:

```python
raise RuntimeError("this block is supposed to fail")
```

One hanging block (must hit the runner timeout):

```python
import time
time.sleep(3600)
```

One block in a language not on the allowlist (must be skipped):

```rust
fn main() { println!("never runs"); }
```
