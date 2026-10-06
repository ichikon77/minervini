# -*- coding: utf-8 -*-
"""投稿に画像が付いているかをX APIで確認する。 使い方: python check_tweet_media.py 2107425783006540210"""
import sys
import tweepy
import kabuchiwa_post as kp

cfg = kp.load_config()
client = tweepy.Client(consumer_key=cfg["consumer_key"], consumer_secret=cfg["consumer_secret"],
                       access_token=cfg["access_token"], access_token_secret=cfg["access_token_secret"])
tid = sys.argv[1]
r = client.get_tweet(tid, expansions=["attachments.media_keys"], media_fields=["type", "url", "width", "height"],
                     tweet_fields=["attachments", "created_at"], user_auth=True)
print("text:", (r.data.text or "")[:80].replace("\n", " "))
print("attachments:", r.data.attachments)
media = (r.includes or {}).get("media", [])
print(f"media {len(media)} 件")
for m in media:
    print("  ", m.type, m.url, getattr(m, "width", None), getattr(m, "height", None))
