#!/system/bin/sh
# Phase 4 - zRAM (compressed swap) for the guest.
#
# WHAT THIS ACTUALLY DOES (and does not):
#   zRAM is a compressed swap device. It lets the guest hold *more* anonymous
#   memory than it has physical RAM by swapping cold pages out to a compressed
#   backing store. It does NOT "compress the Android framework" as commonly
#   claimed: framework/services pages are file-backed and clean, so they are
#   never written to swap. The real benefit here is fewer OOM kills and less
#   reclaim thrash on a small guest, not a smaller framework.
#
#   NOTE: zram0's backing store is the guest's own RAM. A 512MB zram does not
#   give the guest free memory; it gives it a compressed overflow area. Size it
#   well below total RAM (we use 50% by default) so there is room for the
#   compressor's own working set.
#
# Usage (via adb):  adb push apply-zram.sh /data/local/tmp/ && adb shell sh /data/local/tmp/apply-zram.sh
# Optional arg: MB of zram to create (default 50% of MemTotal).

ZRAM_DEV=/dev/block/zram0
ZRAM_SIZE_MB="${1:-0}"
SWAPPINESS=70

getprop ro.build.version.sdk >/dev/null 2>&1

echo "[zram] --- current state ---"
cat /proc/swaps
echo "[zram] swappiness before: $(cat /proc/sys/vm/swappiness 2>/dev/null)"

# Size the compressed area if not given one explicitly.
if [ "$ZRAM_SIZE_MB" -eq 0 ]; then
    MEM_TOTAL_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    ZRAM_SIZE_MB=$((MEM_TOTAL_KB / 1024 / 2))
    echo "[zram] MemTotal=$((MEM_TOTAL_KB/1024))MB -> using 50% = ${ZRAM_SIZE_MB}MB"
fi

# The zram module is normally built into the AOSP emulator kernel. If the
# device node is missing, try to load it; if that fails we degrade gracefully
# rather than failing the whole profile.
if [ ! -e "$ZRAM_DEV" ]; then
    echo "[zram] $ZRAM_DEV not present, attempting insmod zram..."
    insmod /lib/modules/zram.ko num_devices=1 2>/dev/null \
        || modprobe zram num_devices=1 2>/dev/null \
        || echo "[zram] could not load zram module"
fi

if [ ! -e "$ZRAM_DEV" ]; then
    echo "[zram] zram unavailable in this kernel; continuing without compressed swap."
    exit 0
fi

# Prefer lz4: best speed/compression balance for a low-RAM guest. Fall back
# through the algorithms the kernel actually advertises.
PREFERRED="lz4 lz4hc zstd deflate"
CHOOSED=""
for algo in $PREFERRED; do
    if grep -qw "$algo" /sys/block/zram0/comp_algorithm 2>/dev/null; then
        echo "$algo" > /sys/block/zram0/comp_algorithm 2>/dev/null && CHOOSED="$algo" && break
    fi
done
[ -z "$CHOOSED" ] && CHOOSED=$(cat /sys/block/zram0/comp_algorithm 2>/dev/null | tr -d '[]' | cut -d' ' -f1)
echo "[zram] compression algorithm: $CHOOSED"

# Recreate the device cleanly, then size it.
echo 1 > /sys/block/zram0/reset 2>/dev/null
echo "$((ZRAM_SIZE_MB * 1024 * 1024))" > /sys/block/zram0/disksize
echo "[zram] disksize set to ${ZRAM_SIZE_MB}MB"

mkswap "$ZRAM_DEV" >/dev/null 2>&1 && echo "[zram] mkswap ok"
swapon "$ZRAM_DEV" 2>/dev/null && echo "[zram] swapon ok" || echo "[zram] swapon FAILED"

# Swappiness 70, not 100. At 100 the guest reclaims almost eagerly and spends
# its time compressing/uncompressing instead of running. 70 keeps cold pages
# moving to zram while leaving headroom to just reuse already-resident memory.
echo "$SWAPPINESS" > /proc/sys/vm/swappiness 2>/dev/null \
    && echo "[zram] swappiness set to $SWAPPINESS"

# Background-reclaim tuning: let zram compress on a background thread rather
# than stalling the allocating thread. Best-effort; older kernels lack this.
echo 1 > /sys/block/zram0/comp_background_algorithm 2>/dev/null \
    && echo "[zram] background compaction enabled"

echo "[zram] --- final state ---"
cat /proc/swaps
echo "[zram] swappiness after: $(cat /proc/sys/vm/swappiness 2>/dev/null)"
echo "[zram] meminfo_total_used:"
grep -E "MemTotal|MemAvailable|SwapTotal|SwapFree" /proc/meminfo
