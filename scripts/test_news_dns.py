import socket, unittest
from unittest.mock import patch
import news_dns as D

FAKE=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('198.18.0.210',443))]
PUBLIC=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('104.26.9.170',443))]
class DNSTests(unittest.TestCase):
    def setUp(self):self.resolver=D.NewsDNSResolver()
    def test_outside_news_context_keeps_system_resolution(self):
        with patch.object(D,'SYSTEM_GETADDRINFO',return_value=FAKE),patch.object(self.resolver,'public_addresses') as doh:
            self.assertEqual(self.resolver.getaddrinfo('news.example',443),FAKE);doh.assert_not_called()
    def test_fake_ip_in_news_context_uses_checked_public_addresses(self):
        with patch.object(D,'SYSTEM_GETADDRINFO',side_effect=[FAKE,PUBLIC]) as dns,patch.object(self.resolver,'public_addresses',return_value=['104.26.9.170']):
            with D.news_context():self.assertEqual(self.resolver.getaddrinfo('news.example',443),PUBLIC)
            self.assertEqual(dns.call_args.args[0],'104.26.9.170')
    def test_private_ip_and_literal_fake_ip_are_not_replaced(self):
        for host,ip in [('intranet.example','192.168.1.2'),('198.18.0.2','198.18.0.2'),('localhost','198.18.0.3')]:
            rows=[(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,443))]
            with self.subTest(host=host),patch.object(D,'SYSTEM_GETADDRINFO',return_value=rows),patch.object(self.resolver,'public_addresses') as doh:
                with D.news_context():self.assertEqual(self.resolver.getaddrinfo(host,443),rows)
                doh.assert_not_called()
    def test_normal_public_dns_unchanged(self):
        with patch.object(D,'SYSTEM_GETADDRINFO',return_value=PUBLIC),patch.object(self.resolver,'public_addresses') as doh:
            with D.news_context():self.assertEqual(self.resolver.getaddrinfo('news.example',443),PUBLIC)
            doh.assert_not_called()
    def test_doh_rejects_nonpublic_answers_and_mixed_answers(self):
        for answers in [['127.0.0.1'],['198.18.0.2'],['104.26.9.170','10.0.0.1'],[]]:
            with self.subTest(answers=answers),patch.object(self.resolver,'lookup',return_value=answers):
                with self.assertRaises(OSError):self.resolver.public_addresses('news.example')
    def test_doh_failure_does_not_fall_back_to_fake_ip(self):
        with patch.object(D,'SYSTEM_GETADDRINFO',return_value=FAKE),patch.object(self.resolver,'public_addresses',side_effect=OSError('offline')):
            with D.news_context(),self.assertRaises(OSError):self.resolver.getaddrinfo('news.example',443)
    def test_news_wrapper_restores_scope_after_exception(self):
        @D.for_news
        def fail():
            self.assertTrue(D.NEWS_CONTEXT.get());raise ValueError('failed')
        with self.assertRaises(ValueError):fail()
        self.assertFalse(D.NEWS_CONTEXT.get())
    def test_ttl_cache_is_used_then_expires(self):
        with patch.object(self.resolver,'lookup',return_value=['104.26.9.170']) as lookup,patch.object(D.time,'monotonic',side_effect=[0,1,301]):
            for _ in range(3):self.resolver.public_addresses('news.example')
            self.assertEqual(lookup.call_count,2)
if __name__=='__main__':unittest.main()
