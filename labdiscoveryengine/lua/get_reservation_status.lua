------------------------------------------
-- Retrieve the status of a reservation
--
-- arguments: reservation_id
--
-- return:
-- status, external_session_id, position, url
--
------------------------------------------

local reservation_id = ARGV[1]

local external_session_id = nil
local position = nil
local url = nil
local message = nil

local reservation_key = "lde:reservations:" .. reservation_id

local status = redis.call("hget", reservation_key, "status")
message = redis.call("hget", reservation_key, "message")

if status == "pending" or status == "queued" then

    local resources = redis.call("smembers", reservation_key .. ":resources")
    local min_position = nil

    for _, resource in ipairs(resources) do
        local resource_queues = redis.call("zrange", "lde:resources:" .. resource .. ":queues:priorities", 0, -1)
        local total_position = 0
        local found = false

        for _, priority in ipairs(resource_queues) do
            local queue = redis.call("lrange", "lde:resources:" .. resource .. ":queues:" .. priority, 0, -1)
            for pos, res_id in ipairs(queue) do
                if res_id == reservation_id then
                    total_position = total_position + pos - 1 -- Lua tables are 1-indexed
                    found = true
                    break
                end
            end
            if found then
                break
            else
                total_position = total_position + #queue
            end
        end

        if found and (min_position == nil or total_position < min_position) then
            min_position = total_position
        end
    end

    if min_position then
        position = min_position
        status = "queued"
    else
        -- A worker may have dequeued the reservation just before this status
        -- lookup runs, while the reservation hash still says pending/queued.
        -- Keep the request in a non-terminal state and let the next poll read
        -- the updated initializing/ready/broken status from the hash.
        status = "pending"
    end

elseif status == "ready" or status == "cancelling" or status == "finishing" then
    external_session_id = redis.call("hget", reservation_key, "session_id")
    if status == "ready" then
        url = redis.call("hget", reservation_key, "url")
    end
end


return { status or false, external_session_id or false, position or false, url or false, message or false }
