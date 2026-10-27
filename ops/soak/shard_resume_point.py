"""Where a consumer group taking over from other groups must start. Stdlib only.

Used by ops/soak/shards_off.sh when two engine shards hand back to one engine,
and tested by ops/soak/test_shard_groups.sh. It is the same rule the engine
applies when a shard group is first created from the single `engine` group
(infrastructure/redis/event_consumer.py `_resume_point`), turned round and
taken over several groups:

  * a group with pending entries resumes just before its OLDEST pending id --
    that close was delivered and never finished, so its new owner must see it;
  * a group with none resumes at its last-delivered id;
  * over several groups, the EARLIEST of those. Re-running a close another
    shard already finished is safe (every detection write is idempotent and a
    decided candle is not decided again); skipping one is not.

    python3 shard_resume_point.py <last_delivered>:<oldest_pending|-> [...]

prints the id. Ids are Redis stream ids "ms-seq".
"""
import sys


def parse(entry_id: str) -> tuple[int, int]:
    millis, sequence = entry_id.split("-")
    return int(millis), int(sequence)


def just_before(entry_id: str) -> str:
    """The id immediately preceding `entry_id` (event_consumer._just_before)."""
    millis, sequence = parse(entry_id)

    if sequence > 0:
        return f"{millis}-{sequence - 1}"

    if millis == 0:
        return "0"

    return f"{millis - 1}-{2**64 - 1}"


def resume_point(groups: list[tuple[str, str | None]]) -> str:
    """The earliest restart point over (last_delivered, oldest_pending) pairs."""
    if not groups:
        raise ValueError("no groups to take over from")

    points = [just_before(pending) if pending else delivered for delivered, pending in groups]

    return min(points, key=lambda p: (0, 0) if p == "0" else parse(p))


def main(argv: list[str]) -> int:
    pairs = []

    for arg in argv:
        delivered, _, pending = arg.partition(":")
        parse(delivered)  # refuses anything that is not an id
        pairs.append((delivered, None if pending in ("", "-") else pending))

    print(resume_point(pairs))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
