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

if redis.call('hexists', reservation_key, 'recovery_interrupted') == 1 then
    return {'broken', false, false, false, 'Laboratory restarted; please reserve again', false}
end

-- Deadlines terminate the user-facing session, never the hardware lease.
-- Keep a separate durable marker so a late worker reply cannot revive it,
-- and so a live processor can still finish the known backend session safely.
local expired = redis.call('hget', reservation_key, 'deadline_expired')
if not expired and status and status ~= 'finished' and status ~= 'broken' and status ~= 'unavailable' then
    local field = 'session_deadline'
    if status == 'pending' or status == 'queued' or status == 'initializing' then
        field = 'initialization_deadline'
    end
    local raw = redis.call('hget', reservation_key, field)
    if not raw then raw = redis.call('hget', reservation_key, 'initialization_deadline') end
    if raw then
        local deadline = tonumber(raw)
        local now = redis.call('TIME')
        if not deadline or deadline ~= deadline or deadline == math.huge or
           tonumber(now[1]) + tonumber(now[2]) / 1000000 >= deadline then
            expired = field
            redis.call('hsetnx', reservation_key, 'deadline_expired', expired)
            redis.call('publish', reservation_key .. ':channel', 'deadline-expired')
        end
    end
end
if expired then
    return {'broken', false, false, false, 'Session time limit reached; please reserve again',
            redis.call('hget', reservation_key, 'resource') or false}
end

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


local assigned_resource = redis.call("hget", reservation_key, "resource")
return { status or false, external_session_id or false, position or false, url or false, message or false, assigned_resource or false }
