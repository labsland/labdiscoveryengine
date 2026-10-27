"""Atomic scheduler ownership checks for managed laboratory recovery.

An HTTP check and dispatch cannot be atomic with Redis. The immutable epoch
header fences delayed requests at PDLS; a cold boundary kills requests already
accepted there before the new epoch is published. These two parts are required.
"""

class OwnershipLost(Exception):
    """The recovery coordinator terminated this processor's session."""


TRANSITION = """
local run = string.match(redis.call('INFO','server'), 'run_id:([^\\r\\n]+)')
if run ~= ARGV[1] or ARGV[2] == '' then return 0 end
if redis.call('GET',KEYS[1] .. ':assigned') ~= ARGV[3] or
   redis.call('GET',KEYS[1] .. ':recovery-epoch') ~= ARGV[2] or
   redis.call('EXISTS',KEYS[1] .. ':recovery-hold') == 1 or
   redis.call('HGET',KEYS[2],'recovery_epoch') ~= ARGV[2] or
   redis.call('HEXISTS',KEYS[2],'recovery_interrupted') == 1 then return 0 end
if ARGV[4] == 'claim' and redis.call('HSETNX',KEYS[2],'start_attempted','1') == 0 then return -1 end
if ARGV[4] == 'release' then
 redis.call('DEL',KEYS[1] .. ':assigned')
 redis.call('EXPIRE',KEYS[2],3600)
 redis.call('EXPIRE',KEYS[2] .. ':resources',3600)
 redis.call('EXPIRE','lde:external-request:' .. ARGV[3],604800)
 return 1
end
if ARGV[4] == 'quarantine' then
 redis.call('PERSIST',KEYS[1] .. ':assigned')
 redis.call('PERSIST',KEYS[2])
 redis.call('PERSIST','lde:external-request:' .. ARGV[3])
end
for i=5,#ARGV,2 do redis.call('HSET',KEYS[2],ARGV[i],ARGV[i+1]) end
if #ARGV > 4 then
 redis.call('PUBLISH',KEYS[3],redis.call('HGET',KEYS[2],'status') or 'changed')
end
return 1
"""
