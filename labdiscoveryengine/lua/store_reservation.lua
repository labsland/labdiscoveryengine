-----------------------------
-- Store a new reservation
-- 
-- Parameters:
--
-- * reservation_id: str
-- * reservation_metadata: str
-- * laboratory_id: str
-- * priority: int
-- * current_user: str
-- * resources: List[str]
-----------------------------

local reservation_id = ARGV[1]
local reservation_metadata = ARGV[2]
local laboratory = ARGV[3]
local priority = tonumber(ARGV[4])
local current_user = ARGV[5]
local resources = {} -- onwards

local reservation_key = "lde:reservations:" .. reservation_id

for i = 6, #ARGV do
    table.insert(resources, ARGV[i])
end

-- Store basic information in a hashset
redis.call("hset", reservation_key, "status", "pending")
redis.call("hset", reservation_key, "laboratory", laboratory)
redis.call("hset", reservation_key, "metadata", reservation_metadata)
redis.call("expire", reservation_key, 3600)

-- Store the reservation_id in the user reservation_ids
redis.call("sadd", "lde:users:" .. current_user .. ":reservations", reservation_id)
redis.call("expire", "lde:users:" .. current_user .. ":reservations", 3600)

-- Store each resource in the reservation resources
for i, resource in ipairs(resources) do
    redis.call("sadd", reservation_key .. ":resources", resource)
end
redis.call("expire", reservation_key .. ":resources", 3600)


-- Store in the queue of each resource the particual reservation
for i, resource in ipairs(resources) do
    local resource_base = "lde:resources:" .. resource
    local queue_base_key = resource_base .. ":queues:"

    redis.call("rpush", queue_base_key .. priority, reservation_id)
    redis.call("zadd", queue_base_key .. "priorities", priority, priority)
    redis.call("expire", queue_base_key .. priority, 3600)
    redis.call("expire", queue_base_key .. "priorities", 3600)
    redis.call("publish", resource_base .. ":channel", reservation_id)
end
