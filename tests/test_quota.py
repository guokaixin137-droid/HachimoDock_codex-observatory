from datetime import datetime, timedelta, timezone
import unittest
from bridge import document
from collect import normalize_quota
from quota_layout import MINI_DECODE


class AdaptiveQuotaTests(unittest.TestCase):
    now=datetime(2026,11,16,10,tzinfo=timezone.utc)

    def raw(self,minutes,used):
        return {'window_minutes':minutes,'used_percent':used,'resets_at':int((self.now+timedelta(days=1)).timestamp())}

    def view(self,primary=None,secondary=None,at=None):
        quota=normalize_quota((at or self.now,{'primary':primary,'secondary':secondary}),self.now)
        return document({'quota':quota},self.now)['adaptive_quota']

    def test_week_only_uses_single_card(self):
        v=self.view(self.raw(10080,47));self.assertEqual(v['mode'],53)
        self.assertIn('周额度',v['v2'])

    def test_short_only_does_not_invent_a_weekly_limit(self):
        v=self.view(self.raw(300,10));self.assertEqual(v['mode'],90)
        self.assertIn('5 小时额度',v['v2']);self.assertNotIn('周',v['v2'])

    def test_empty_second_window_is_not_a_second_card(self):
        self.assertEqual(self.view(self.raw(10080,47),{})['mode'],53)

    def test_both_windows_include_an_exhausted_week(self):
        v=self.view(self.raw(300,10),self.raw(10080,100))
        self.assertEqual(v['mode'],101)
        self.assertTrue(v['v2'].startswith('90%'));self.assertIn('0%',v['v2'])
        self.assertEqual(len(v['v2'].encode()),63)
        self.assertEqual(''.join(MINI_DECODE[c] for c in v['v2'][23:]).strip(),'11/17 18:00              11/17 18:00')

    def test_primary_secondary_order_does_not_reverse_period_labels(self):
        normal=self.view(self.raw(300,10),self.raw(10080,47))
        reversed_view=self.view(self.raw(10080,47),self.raw(300,10))
        self.assertEqual(normal,reversed_view)

    def test_full_zero_and_unknown_values_fit(self):
        for a,b in [(0,0),(100,100),(None,99)]:
            v=self.view(self.raw(300,a),self.raw(10080,b))
            self.assertEqual(len(v['v2'].encode()),63)
            self.assertEqual(v['mode'],103 if a is None else 101)

    def test_stale_data_retains_both_cards_but_is_marked(self):
        v=self.view(self.raw(300,10),self.raw(10080,47),self.now-timedelta(minutes=6))
        self.assertEqual(v['mode'],102)

    def test_expired_window_is_not_treated_as_absent_or_full(self):
        raw=self.raw(300,10);raw['resets_at']=int(self.now.timestamp())
        v=self.view(raw,self.raw(10080,47))
        self.assertEqual(v['mode'],103);self.assertTrue(v['v2'].startswith('--'))

    def test_unavailable_does_not_claim_no_account_limits(self):
        v=document({},self.now)['adaptive_quota']
        self.assertEqual(v['mode'],-1);self.assertEqual(v['v2'],'额度数据暂不可用')

    def test_other_periods_are_not_mislabeled_as_five_hours(self):
        v=self.view(self.raw(1440,10),self.raw(10080,47))
        self.assertEqual(v['mode'],104)
        dates=''.join(MINI_DECODE[c] for c in v['v2'][23:])
        self.assertTrue(dates.startswith('1D  '));self.assertIn('7D  ',dates)


if __name__=='__main__':unittest.main()
