-------------------------------------
-- Assign a reservation to a resource
--
-- Checks if there is any reservation
-- to be assigned and assign it (if 
-- nobody else assigned it first) 
--
-- Parameters:
--
-- * resource: str
-------------------------------------

local resource = ARGV[1]
-- Validate before popping queues: a bad deployment setting must not lose work.
local initialization_timeout = tonumber(ARGV[4] or '300')
if not initialization_timeout or initialization_timeout <= 0 or
   initialization_timeout ~= initialization_timeout or initialization_timeout == math.huge then
    return redis.error_reply('Invalid initialization timeout')
end

-- Managed recovery deployments bind admission to the Redis process whose
-- durable maintenance journals were reconciled at worker startup. A Redis
-- restart must not admit work from an older AOF snapshot before reconciliation.
if ARGV[2] and ARGV[2] ~= '' then
    local run_id = string.match(redis.call('INFO', 'server'), 'run_id:(%x+)')
    if run_id ~= ARGV[2] or redis.call('get', 'lde:running') ~= 'true' then
        return false
    end
end

-- Ownership survives uncertain startup/cleanup and worker restart. Never pop
-- another queue while this physical resource is assigned, even if its previous
-- reservation is marked broken. Only verified cleanup may release the marker.
if redis.call("exists", "lde:resources:" .. resource .. ":assigned") == 1 or
   redis.call("exists", "lde:resources:" .. resource .. ":maintenance") == 1 then
    return false
end

-- Recovery is independent of maintenance: neither mechanism may clear the other.
if redis.call('exists', 'lde:resources:' .. resource .. ':recovery-hold') == 1 then
    return false
end
if redis.call('exists','lde:resources:' .. resource .. ':recovery-epoch') == 1 and
   (not ARGV[3] or ARGV[3] == '') then return false end
if ARGV[3] and ARGV[3] ~= '' then
    if redis.call('get', 'lde:resources:' .. resource .. ':recovery-epoch') ~= ARGV[3] then
        return false
    end
end

local reservation_id = false

local priorities = redis.call("zrange", "lde:resources:" .. resource .. ":queues:priorities", 0, -1)

-- we go queue by queue looking for tasks to do
for _, priority in ipairs(priorities) do
    local assigned = 0
    while assigned == 0 do
        reservation_id = redis.call("lpop", "lde:resources:" .. resource .. ":queues:" .. priority)
        if reservation_id == false then
            -- There was no pending reservation. Do not continue
            -- with this priority queue and go to the next one
            break
        end

        local key = "lde:reservations:" .. reservation_id
        local status = redis.call("hget", key, "status")
        local metadata = redis.call("hget", key, "metadata")
        -- Shared queues can outlive individual requests. Never recreate an
        -- expired hash or claim hardware for a terminal/unowned stale entry.
        if metadata and (status == "pending" or status == "queued" or status == "cancelling") then
            assigned = redis.call("hsetnx", key, ":assigned", 1)
            if assigned ~= 0 then
                -- Claim and retention are one atomic operation. The existing
                -- one-hour queue deadline must not expire an owned session.
                local now = redis.call('TIME')
                redis.call('hset', key, 'initialization_deadline',
                    tonumber(now[1]) + tonumber(now[2]) / 1000000 + initialization_timeout)
                redis.call("persist", key)
                redis.call("persist", key .. ":resources")
                redis.call("persist", "lde:external-request:" .. reservation_id)
            end
        end
        if assigned ~= 0 then -- It was previously assigned in another queue
            -- but if it did not exist, it means that no other resource was assigned to this reservation and we will use this one
            break
        end
    end

    if assigned ~= 0 then
        -- we got one assigned, stop checking other queues
        break
    end
end

if reservation_id ~= false then
    if ARGV[3] and ARGV[3] ~= '' then
        redis.call('hset', 'lde:reservations:' .. reservation_id, 'recovery_epoch', ARGV[3])
    end
    redis.call("set", "lde:resources:" .. resource .. ":assigned", reservation_id)
end

return reservation_id
