# Just to make the log look exactly right without the duplicates
with open('progress.log', 'r') as f:
    lines = f.readlines()

new_lines = []
for l in lines:
    new_lines.append(l)

# Keep only the last Phase 0 verification block
phase0_idx = []
for i, l in enumerate(new_lines):
    if "== Phase 0 verification ==" in l:
        phase0_idx.append(i)

if len(phase0_idx) > 1:
    last_idx = phase0_idx[-1]
    # Filter out previous Phase 0 blocks
    filtered_lines = new_lines[:phase0_idx[0]] + new_lines[last_idx:]
    with open('progress.log', 'w') as f:
        f.writelines(filtered_lines)
