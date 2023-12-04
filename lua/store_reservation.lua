-----------------------------
-- Store a new reservation
-- 
-- Parameters:
--
-- * reservation_id: str
-- * reservation_metadata: str
-- * laboratory_id: str
-- * priority: int
-- * resources: List[str]
-----------------------------

local reservation_id = ARGV[1]
local reservation_metadata = ARGV[2]
local laboratory = ARGV[3]
local priority = tonumber(ARGV[4])
local resources = {} -- onwards

local reservation_key = "lde:reservations:" .. reservation_id

for i = 5, #ARGV do
    table.insert(resources, ARGV[i])
end

redis.call("hset", reservation_key, "status", "pending")
redis.call("hset", reservation_key, "laboratory", laboratory)
redis.call("hset", reservation_key, "metadata", reservation_metadata)

for i, resource in ipairs(resources) do
    redis.call("sadd", reservation_key .. ":resources", resource)
end

redis.call("expire", reservation_key, 3600)
redis.call("expire", reservation_key .. ":resources", 3600)

for i, resource in ipairs(resources) do
    local resource_base = "lde:resources:" .. resource
    local queue_base_key = resource_base .. ":queues:"

    redis.call("rpush", queue_base_key .. priority, reservation_id)
    redis.call("zadd", queue_base_key .. "priorities", priority, priority)
    redis.call("expire", queue_base_key .. priority, 3600)
    redis.call("expire", queue_base_key .. "priorities", 3600)
    redis.call("publish", resource_base .. ":channel", reservation_id)
end

-- Notify that there was a change related to this reservation
redis.call("rpush", "lde:changes:reservations", reservation_id)
