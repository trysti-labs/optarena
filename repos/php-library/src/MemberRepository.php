<?php

namespace App;

require_once __DIR__ . '/Member.php';

class MemberRepository
{
    /** @var array<int, Member> */
    private array $members = [];
    private int $nextId = 1;

    public function create(string $name, string $email): Member
    {
        $member = new Member($this->nextId, $name, $email);
        $this->members[$member->id] = $member;
        $this->nextId++;
        return $member;
    }

    public function find(int $id): ?Member
    {
        return $this->members[$id] ?? null;
    }

    /** @return Member[] */
    public function all(): array
    {
        $members = array_values($this->members);
        usort($members, fn(Member $a, Member $b) => $a->id <=> $b->id);
        return $members;
    }
}
