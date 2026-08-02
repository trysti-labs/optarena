<?php

namespace App;

class Member
{
    public int $id;
    public string $name;
    public string $email;
    public bool $active;

    public function __construct(int $id, string $name, string $email)
    {
        $this->id = $id;
        $this->name = $name;
        $this->email = $email;
        $this->active = true;
    }
}
