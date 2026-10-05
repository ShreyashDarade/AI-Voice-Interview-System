"""Objects in view: phones, reference material, extra people."""
from __future__ import annotations

from ..framework.detector import FrameContext, FrameDetector, register
from ..framework.types import Category, Severity, Signal, Source
from ..framework.util import SlidingHits, clamp01


@register
class ObjectsDetector(FrameDetector):
    name = 'objects'
    emits = ('phone_detected', 'reference_material', 'additional_person')
    requires = frozenset({'objects'})

    PHONE_MIN_SCORE = 0.45
    PHONE_MIN_AREA = 0.002

    def __init__(self, config=None):
        super().__init__(config)
        win, hits = self.config.get('window', 5), self.config.get('min_hits', 3)
        self._phone = SlidingHits(win, hits)
        self._ref = SlidingHits(win, hits)
        self._person = SlidingHits(win + 2, hits + 1)

    def reset(self):
        for h in (self._phone, self._ref, self._person):
            h.clear()

    def process(self, ctx: FrameContext):
        if not ctx.get('objects_ran'):
            return []
        objs, ts, out = ctx.get('objects'), ctx.frame.ts, []
        n_faces = ctx.get('n_faces', 0)

        phones = [o for o in objs if o.label == 'cell phone' and o.score >= self.PHONE_MIN_SCORE
                  and o.area >= self.PHONE_MIN_AREA]
        refs = [o for o in objs if (o.label == 'book' and o.score >= 0.5)
                or (o.label in ('laptop', 'tv') and o.score >= 0.55)]
        persons = [o for o in objs if o.label == 'person' and o.score >= 0.5 and o.area >= 0.03]

        if self._phone.push(bool(phones)) and phones:
            best = max(phones, key=lambda o: o.score)
            conf = 0.55 + 0.4 * (best.score - self.PHONE_MIN_SCORE) / (1 - self.PHONE_MIN_SCORE)
            out.append(Signal(kind='phone_detected', category=Category.ENVIRONMENT, severity=Severity.HIGH,
                              confidence=clamp01(conf), source=Source.VISION,
                              details={'score': round(best.score, 2), 'bbox': [round(v, 3) for v in best.bbox]},
                              ts=ts, capture_evidence=True))
        if self._ref.push(bool(refs)) and refs:
            best = max(refs, key=lambda o: o.score)
            out.append(Signal(kind='reference_material', category=Category.ENVIRONMENT, severity=Severity.MEDIUM,
                              confidence=clamp01(0.5 + 0.3 * best.score), source=Source.VISION,
                              details={'label': best.label, 'score': round(best.score, 2)}, ts=ts,
                              capture_evidence=True))
        # more person bodies than faces -> someone beside/behind the candidate
        if self._person.push(len(persons) > max(1, n_faces)) and len(persons) > max(1, n_faces):
            out.append(Signal(kind='additional_person', category=Category.PRESENCE, severity=Severity.MEDIUM,
                              confidence=0.6, source=Source.VISION,
                              details={'persons': len(persons), 'faces': n_faces}, ts=ts, capture_evidence=True))
        return out
