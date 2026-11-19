#!/bin/sh
# Pack each alpha WebM (VP9) into an H.264 MP4 for Safari and iOS:
# premultiplied colour in the top half, alpha as grey in the bottom half.
# src/AlphaVideo.jsx recombines the halves in WebGL. Rerun after re-exporting
# a lyric or highlight WebM. Needs ffmpeg with libvpx-vp9 (to decode alpha)
# and libx264.
set -e
cd "$(dirname "$0")/../public/assets"
for src in lyrics/chace-original lyrics/chace-translated lyrics/alicks-original lyrics/alicks-translated highlight/music-highlight; do
  ffmpeg -v error -y -c:v libvpx-vp9 -i "$src.webm" \
    -filter_complex "[0:v]format=gbrap,premultiply=inplace=1,split[c][a];[a]alphaextract,format=gbrp[al];[c]format=gbrp[cc];[cc][al]vstack,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p[v]" \
    -map "[v]" -an -c:v libx264 -preset slow -crf 16 -g 30 -profile:v high \
    -colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv \
    -movflags +faststart "$src-packed.mp4"
done
