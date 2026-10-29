"""Keep the loop-detection optimization equivalent to the previous implementation."""
import random
from app import selection


def reference_mask(tokens, repeats=4, max_n=6):
    # Baseline algorithm from b0dbd04, before the early word comparison optimization.
    mask=[False]*len(tokens)
    i=0
    while i<len(tokens):
        jumped=False
        for n in range(1,max_n+1):
            unit=tokens[i:i+n]
            if len(unit)<n: break
            count=1
            while tokens[i+count*n:i+(count+1)*n]==unit: count+=1
            if count>=repeats:
                for k in range(i+n,i+count*n): mask[k]=True
                i+=count*n;jumped=True;break
        if not jumped: i+=1
    return mask


def test_optimized_loop_detection_matches_baseline():
    rng=random.Random(20261029)
    samples=[[],['bora']*40,['vou','agachar']*40,['a','b','c']*5+['fim']]
    samples += [[rng.choice(['a','b','c','x','fim']) for _ in range(rng.randrange(150))] for _ in range(500)]
    for sample in samples:
        for repeats in (2,4,6):
            assert selection.loop_mask(sample,min_repeats=repeats)==reference_mask(sample,repeats)
