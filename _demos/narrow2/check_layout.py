"""Check interactive hit rectangles in all requested narrow sizes."""
from common import rects

def overlaps(a,b):
    return a[0]<b[0]+b[2] and b[0]<a[0]+a[2] and a[1]<b[1]+b[3] and b[1]<a[1]+a[3]

errors=[]
for variant in 'ABC':
    for w,h in ((360,640),(320,640),(480,800)):
        items=rects(variant,w,h)
        for name,r in items.items():
            if r[0]<0 or r[1]<0 or r[0]+r[2]>w or r[1]+r[3]>h: errors.append(f'{variant} {w}x{h}: {name} out of bounds')
        for i,(name,r) in enumerate(items.items()):
            for other,s in list(items.items())[i+1:]:
                if overlaps(r,s): errors.append(f'{variant} {w}x{h}: {name} overlaps {other}')
        print(f'{variant} {w}x{h}: checked {len(items)} controls')
if errors:
    print('\n'.join(errors)); raise SystemExit(1)
print('PASS: all interactive control rectangles are inside the window and disjoint')
