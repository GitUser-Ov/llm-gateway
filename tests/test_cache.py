import app.cache.answer_cache as answer_cache

async def test_cache_ttl_is_set(test_redis):
    messages=[{"role":"user","content":"TTL 测试"}]
    model="gpt-4o-mini"
    temperature=0.3

    await answer_cache.set_answer(
        test_redis,messages,model,temperature,"缓存里的答案"
    )

    key=answer_cache.make_key(messages,model,temperature)
    ttl=await test_redis.ttl(key)
    
    assert ttl>0
    assert 3000<=ttl<=3900
