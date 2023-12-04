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

        -- hsetnx will return 0 if it already existed
        assigned = redis.call("hsetnx", "lde:reservations:" .. reservation_id, ":assigned", 1)
        if assigned ~= 0 then -- It was previously assigned in another queue
            -- but if it did not exist, it means that no other resource was assigned to this reservation and we will use this one
            break
        end
        -- TODO: maybe check other constraints, such as is the reservations still valid, etc.
    end

    if assigned ~= 0 then
        -- we got one assigned, stop checking other queues
        break
    end
end

if reservation_id ~= false then
    redis.call("setex", "lde:resources:" .. resource .. ":assigned", 3600, reservation_id)
end

return reservation_id